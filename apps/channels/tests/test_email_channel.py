from unittest.mock import MagicMock

import pytest

from apps.channels.datamodels import EmailMessage
from apps.channels.email import get_email_experiment_channel
from apps.channels.forms import EmailChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.experiment import ExperimentFactory


def _make_inbound_message(
    from_email="sender@example.com",
    to_email="bot@chat.openchatstudio.com",
    subject="Hello",
    text="Hi there",
    message_id="<msg1@example.com>",
    in_reply_to=None,
    references="",
):
    """Create a mock AnymailInboundMessage."""
    msg = MagicMock()
    msg.from_email.addr_spec = from_email
    msg.to = [MagicMock()]
    msg.to[0].addr_spec = to_email
    msg.subject = subject
    msg.text = text
    msg.get = MagicMock(
        side_effect=lambda key, default=None: {
            "Message-ID": message_id,
            "In-Reply-To": in_reply_to,
            "References": references,
        }.get(key, default)
    )
    return msg


class TestEmailMessageParse:
    def test_basic_parse(self):
        inbound = _make_inbound_message()
        result = EmailMessage.parse(inbound)

        assert result.participant_id == "sender@example.com"
        assert result.message_text == "Hi there"
        assert result.from_address == "sender@example.com"
        assert result.to_address == "bot@chat.openchatstudio.com"
        assert result.subject == "Hello"
        assert result.message_id == "<msg1@example.com>"
        assert result.in_reply_to is None
        assert result.references == []

    def test_parse_with_reply_headers(self):
        inbound = _make_inbound_message(
            in_reply_to="<original@example.com>",
            references="<original@example.com> <reply1@example.com>",
        )
        result = EmailMessage.parse(inbound)

        assert result.in_reply_to == "<original@example.com>"
        assert result.references == ["<original@example.com>", "<reply1@example.com>"]

    def test_parse_strips_quoted_text(self):
        body_with_quote = "New reply text\n\nOn Mon, Apr 21, 2026, user wrote:\n> Original message"
        inbound = _make_inbound_message(text=body_with_quote)
        result = EmailMessage.parse(inbound)

        # mail-parser-reply should strip the quoted portion
        assert "Original message" not in result.message_text
        assert "New reply text" in result.message_text

    def test_parse_no_to_address(self):
        inbound = _make_inbound_message()
        inbound.to = []
        result = EmailMessage.parse(inbound)
        assert result.to_address == ""

    def test_parse_empty_subject(self):
        inbound = _make_inbound_message(subject=None)
        result = EmailMessage.parse(inbound)
        assert result.subject == ""

    def test_parse_empty_references(self):
        inbound = _make_inbound_message(references="")
        result = EmailMessage.parse(inbound)
        assert result.references == []

    def test_parse_fallback_to_full_body_when_no_reply(self):
        """When there is no quoted text, the full body is preserved."""
        inbound = _make_inbound_message(text="Just a simple message")
        result = EmailMessage.parse(inbound)
        assert result.message_text == "Just a simple message"


@pytest.mark.django_db()
class TestEmailChannelForm:
    def test_valid_form(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "platform": "email",
            },
        )
        assert form.is_valid(), form.errors

    def test_email_address_required(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"platform": "email"},
        )
        assert not form.is_valid()
        assert "email_address" in form.errors

    def test_from_address_optional(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "platform": "email",
            },
        )
        assert form.is_valid()
        assert form.cleaned_data.get("from_address", "") == ""

    def test_is_default_defaults_to_false(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "platform": "email",
            },
        )
        assert form.is_valid()
        assert form.cleaned_data["is_default"] is False


@pytest.mark.django_db()
class TestEmailRouting:
    def _make_channel(self, team, experiment=None, email_address="bot@chat.openchatstudio.com", is_default=False):
        """Helper to create an email ExperimentChannel."""
        if experiment is None:
            experiment = ExperimentFactory(team=team)
        extra = {"email_address": email_address}
        if is_default:
            extra["is_default"] = True
        return ExperimentChannel.objects.create(
            team=team,
            experiment=experiment,
            platform=ChannelPlatform.EMAIL,
            extra_data=extra,
            name=f"email-{email_address}",
        )

    def _make_session(self, team, channel, external_id, participant_email="user@example.com"):
        """Helper to create a session with the required related objects."""
        participant, _ = Participant.objects.get_or_create(
            team=team,
            identifier=participant_email,
            platform=ChannelPlatform.EMAIL,
        )
        chat = Chat.objects.create(team=team, name="test chat")
        return ExperimentSession.objects.create(
            team=team,
            experiment=channel.experiment,
            experiment_channel=channel,
            external_id=external_id,
            participant=participant,
            chat=chat,
        )

    def test_priority_1_in_reply_to_match(self, team_with_users):
        team = team_with_users
        channel = self._make_channel(team)
        session = self._make_session(team, channel, "<abc123@chat.openchatstudio.com>")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to="<abc123@chat.openchatstudio.com>",
            references=[],
            to_address="bot@chat.openchatstudio.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session == session

    def test_priority_1b_references_fallback(self, team_with_users):
        team = team_with_users
        channel = self._make_channel(team)
        session = self._make_session(team, channel, "<root@chat.openchatstudio.com>")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to="<nonexistent@example.com>",
            references=["<root@chat.openchatstudio.com>", "<reply1@example.com>"],
            to_address="bot@chat.openchatstudio.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session == session

    def test_priority_2_to_address_match(self, team_with_users):
        team = team_with_users
        channel = self._make_channel(team, email_address="support@chat.openchatstudio.com")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to=None,
            references=[],
            to_address="support@chat.openchatstudio.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session is None

    def test_priority_3_default_fallback(self, team_with_users):
        team = team_with_users
        channel = self._make_channel(team, email_address="default@chat.openchatstudio.com", is_default=True)

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to=None,
            references=[],
            to_address="unknown@chat.openchatstudio.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session is None

    def test_priority_4_no_match(self, team_with_users):
        team = team_with_users

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to=None,
            references=[],
            to_address="unknown@chat.openchatstudio.com",
            team=team,
        )
        assert result_channel is None
        assert result_session is None

    def test_no_default_fallback_without_team(self, team_with_users):
        """Default fallback requires team to be specified."""
        team = team_with_users
        self._make_channel(team, is_default=True)

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to=None,
            references=[],
            to_address="unknown@chat.openchatstudio.com",
            team=None,
        )
        assert result_channel is None
        assert result_session is None
