from unittest.mock import MagicMock, patch

import pytest
from django.core import mail

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.datamodels import EmailMessage
from apps.channels.email import EmailChannel, EmailSender, email_inbound_handler, get_email_experiment_channel
from apps.channels.forms import EmailChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_email_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.channels import ExperimentChannelFactory
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


class TestEmailSender:
    def test_send_text_new_conversation(self):
        """First message in a conversation — no threading headers."""
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        sender.send_text("Hello!", "user@example.com")

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.body == "Hello!"
        assert sent.from_email == "bot@chat.openchatstudio.com"
        assert sent.to == ["user@example.com"]
        assert "Message-ID" in sent.extra_headers

    def test_send_text_threaded_reply(self):
        """Reply to an existing thread — sets In-Reply-To and References."""
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
            subject="Re: Help request",
            in_reply_to="<inbound123@example.com>",
            references=["<root@chat.openchatstudio.com>", "<inbound123@example.com>"],
        )
        sender.send_text("Here's the answer", "user@example.com")

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.subject == "Re: Help request"
        assert sent.extra_headers["In-Reply-To"] == "<inbound123@example.com>"
        assert "<root@chat.openchatstudio.com>" in sent.extra_headers["References"]

    def test_last_message_id_captured(self):
        """After sending, last_message_id holds the outbound Message-ID."""

        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        sender.send_text("Test", "user@example.com")

        assert sender.last_message_id is not None
        assert sender.last_message_id.startswith("<")
        assert sender.last_message_id.endswith(">")


class TestEmailChannel:
    def test_capabilities(self):
        """EmailChannel should have text-only capabilities, no voice, no consent."""

        channel_mock = MagicMock()
        channel_mock.extra_data = {
            "email_address": "bot@chat.openchatstudio.com",
            "from_address": "bot@chat.openchatstudio.com",
        }
        experiment_mock = MagicMock()

        email_channel = EmailChannel(experiment_mock, channel_mock)
        caps = email_channel._get_capabilities()

        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is False
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types

    def test_get_sender_returns_email_sender(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {
            "email_address": "bot@chat.openchatstudio.com",
        }
        experiment_mock = MagicMock()

        email_channel = EmailChannel(experiment_mock, channel_mock)
        sender = email_channel._get_sender()

        assert isinstance(sender, EmailSender)

    def test_get_callbacks_returns_noop(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {}
        experiment_mock = MagicMock()

        email_channel = EmailChannel(experiment_mock, channel_mock)
        callbacks = email_channel._get_callbacks()

        assert isinstance(callbacks, ChannelCallbacks)


@pytest.mark.django_db()
class TestHandleEmailMessageTask:
    def test_routes_and_processes_message(self, team_with_users):
        """Integration test: task finds channel and processes the message."""
        team = team_with_users
        experiment = ExperimentFactory(team=team)
        channel = ExperimentChannelFactory(
            experiment=experiment,
            platform=ChannelPlatform.EMAIL,
            extra_data={"email_address": "bot@chat.openchatstudio.com"},
            team=team,
        )

        email_data = {
            "participant_id": "sender@example.com",
            "message_text": "Hello bot",
            "from_address": "sender@example.com",
            "to_address": "bot@chat.openchatstudio.com",
            "subject": "Test",
            "message_id": "<msg1@example.com>",
            "in_reply_to": None,
            "references": [],
        }

        with patch("apps.channels.email.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            handle_email_message(
                email_data=email_data,
                channel_id=channel.id,
                session_id=None,
            )
            MockEmailChannel.assert_called_once()
            mock_instance.new_user_message.assert_called_once()

    def test_no_channel_logs_and_returns(self):
        """Task with nonexistent channel_id just logs and returns."""
        email_data = {
            "participant_id": "sender@example.com",
            "message_text": "Hello",
            "from_address": "sender@example.com",
            "to_address": "bot@chat.openchatstudio.com",
            "subject": "Test",
            "message_id": "<msg1@example.com>",
            "in_reply_to": None,
            "references": [],
        }
        # Should not raise
        handle_email_message(
            email_data=email_data,
            channel_id=999999,
            session_id=None,
        )


@pytest.mark.django_db()
class TestEmailInboundHandler:
    def test_routes_and_enqueues_task(self, team_with_users):
        """Signal handler finds channel and enqueues the celery task."""
        team = team_with_users
        experiment = ExperimentFactory(team=team)
        ExperimentChannelFactory(
            experiment=experiment,
            platform=ChannelPlatform.EMAIL,
            extra_data={"email_address": "bot@chat.openchatstudio.com"},
            team=team,
        )

        inbound = _make_inbound_message(
            to_email="bot@chat.openchatstudio.com",
        )

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_called_once()

    def test_no_match_silently_ignored(self):
        """Unmatched email is silently ignored (no bounce loop)."""
        inbound = _make_inbound_message(
            to_email="unknown@nowhere.com",
        )

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_not_called()
