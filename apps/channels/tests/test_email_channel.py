from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.db import IntegrityError  # noqa: F811 - used at runtime in test

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.email_channel import (
    EmailChannel,
    EmailSender,
    EmailThreadContext,
    _is_blocked,
    _persist_inbound_attachments,
    email_inbound_handler,
    get_email_experiment_channel,
)
from apps.channels.datamodels import EmailMessage, RawAttachment
from apps.channels.forms import EmailChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_email_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant
from apps.files.models import File
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory


def _make_inbound_message(
    from_email="sender@example.com",
    to_email="bot@chat.openchatstudio.com",
    subject="Hello",
    text="Hi there",
    message_id="<msg1@example.com>",
    in_reply_to=None,
    references="",
    spam_detected=None,
):
    """Create a mock AnymailInboundMessage."""
    msg = MagicMock()
    msg.from_email.addr_spec = from_email
    msg.to = [MagicMock()]
    msg.to[0].addr_spec = to_email
    msg.subject = subject
    msg.text = text
    msg.spam_detected = spam_detected
    msg.get = MagicMock(
        side_effect=lambda key, default=None: {
            "Message-ID": message_id,
            "In-Reply-To": in_reply_to,
            "References": references,
        }.get(key, default)
    )
    return msg


def _make_inbound_with_attachments(parts, **kwargs):
    msg = _make_inbound_message(**kwargs)
    msg.attachments = parts
    return msg


def _mime_part(filename="file.bin", content_type="application/octet-stream", content=b"bytes"):
    part = MagicMock()
    part.get_filename.return_value = filename
    part.get_content_type.return_value = content_type
    part.get_content_bytes.return_value = content
    return part


def _make_email_channel(team, experiment=None, email_address="bot@chat.openchatstudio.com", is_default=False):
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


def _make_session(team, channel, external_id, participant_email="user@example.com"):
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
        inbound = _make_inbound_message(text="Just a simple message")
        result = EmailMessage.parse(inbound)
        assert result.message_text == "Just a simple message"

    def test_parse_extracts_attachments(self):
        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-")
        csv = _mime_part(filename="data.csv", content_type="text/csv", content=b"a,b,c")
        inbound = _make_inbound_with_attachments([pdf, csv])

        result = EmailMessage.parse(inbound)

        assert len(result._raw_attachments) == 2
        assert result._raw_attachments[0].filename == "report.pdf"
        assert result._raw_attachments[0].content_type == "application/pdf"
        assert result._raw_attachments[0].content_bytes == b"%PDF-"
        assert result._raw_attachments[1].filename == "data.csv"

    def test_parse_no_attachments(self):
        inbound = _make_inbound_with_attachments([])
        result = EmailMessage.parse(inbound)
        assert result._raw_attachments == []

    def test_parse_strips_content_type_params(self):
        part = _mime_part(content_type="text/csv; charset=utf-8")
        inbound = _make_inbound_with_attachments([part])
        result = EmailMessage.parse(inbound)
        assert result._raw_attachments[0].content_type == "text/csv"

    def test_parse_handles_missing_filename(self):
        part = _mime_part(filename=None)
        inbound = _make_inbound_with_attachments([part])
        result = EmailMessage.parse(inbound)
        assert result._raw_attachments[0].filename == "attachment"


@pytest.mark.django_db()
class TestEmailChannelForm:
    def test_valid_form(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    def test_email_address_required(self, experiment):
        form = EmailChannelForm(experiment=experiment, data={"platform": "email"})
        assert not form.is_valid()
        assert "email_address" in form.errors

    def test_from_address_optional(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid()
        assert form.cleaned_data.get("from_address", "") == ""

    def test_is_default_defaults_to_false(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid()
        assert form.cleaned_data["is_default"] is False

    def test_duplicate_default_rejected(self, experiment):
        """Only one default email channel per team."""
        _make_email_channel(experiment.team, email_address="first@chat.openchatstudio.com", is_default=True)

        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "second@chat.openchatstudio.com",
                "is_default": True,
                "platform": "email",
            },
        )
        assert not form.is_valid()
        assert "is_default" in form.errors

    def test_duplicate_default_allowed_when_editing_same_channel(self, experiment):
        """Editing the existing default channel should not trigger uniqueness error."""
        channel = _make_email_channel(
            experiment.team, experiment=experiment, email_address="first@chat.openchatstudio.com", is_default=True
        )

        form = EmailChannelForm(
            experiment=experiment,
            channel=channel,
            data={
                "email_address": "first@chat.openchatstudio.com",
                "is_default": True,
                "platform": "email",
            },
        )
        assert form.is_valid(), form.errors


@pytest.mark.django_db()
class TestEmailRouting:
    def test_priority_1_in_reply_to_match(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(team, channel, "<abc123@chat.openchatstudio.com>")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to="<abc123@chat.openchatstudio.com>",
            references=[],
            to_address="bot@chat.openchatstudio.com",
            sender_address="user@example.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session == session

    def test_priority_1_rejects_mismatched_sender(self, team_with_users):
        """Session hijack prevention: spoofed In-Reply-To from wrong sender."""
        team = team_with_users
        channel = _make_email_channel(team)
        _make_session(team, channel, "<abc123@chat.openchatstudio.com>", participant_email="real-user@example.com")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to="<abc123@chat.openchatstudio.com>",
            references=[],
            to_address="bot@chat.openchatstudio.com",
            sender_address="attacker@evil.com",
            team=team,
        )
        # Falls through to Priority 2 (to-address match) instead
        assert result_channel == channel
        assert result_session is None

    def test_priority_1b_references_fallback(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(team, channel, "<root@chat.openchatstudio.com>")

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to="<nonexistent@example.com>",
            references=["<root@chat.openchatstudio.com>", "<reply1@example.com>"],
            to_address="bot@chat.openchatstudio.com",
            sender_address="user@example.com",
            team=team,
        )
        assert result_channel == channel
        assert result_session == session

    def test_priority_2_to_address_match(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team, email_address="support@chat.openchatstudio.com")

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
        channel = _make_email_channel(team, email_address="default@chat.openchatstudio.com", is_default=True)

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

    def test_default_fallback_is_global(self, team_with_users):
        """Default fallback channel is found regardless of team parameter."""
        team = team_with_users
        channel = _make_email_channel(team, is_default=True)

        result_channel, result_session = get_email_experiment_channel(
            in_reply_to=None,
            references=[],
            to_address="unknown@chat.openchatstudio.com",
        )
        assert result_channel == channel
        assert result_session is None


class TestEmailThreadContext:
    def test_from_inbound_adds_re_prefix(self):
        msg = MagicMock()
        msg.subject = "Help request"
        msg.message_id = "<msg1@example.com>"
        msg.references = []

        ctx = EmailThreadContext.from_inbound(msg)
        assert ctx.subject == "Re: Help request"
        assert ctx.in_reply_to == "<msg1@example.com>"
        assert ctx.references == ["<msg1@example.com>"]

    def test_from_inbound_preserves_existing_re_prefix(self):
        msg = MagicMock()
        msg.subject = "Re: Help request"
        msg.message_id = "<msg2@example.com>"
        msg.references = ["<msg1@example.com>"]

        ctx = EmailThreadContext.from_inbound(msg)
        assert ctx.subject == "Re: Help request"

    def test_from_inbound_handles_case_variants(self):
        for prefix in ["RE:", "re:", "Aw:", "Sv:", "FW:"]:
            msg = MagicMock()
            msg.subject = f"{prefix} Something"
            msg.message_id = "<msg@example.com>"
            msg.references = []

            ctx = EmailThreadContext.from_inbound(msg)
            assert not ctx.subject.startswith("Re: "), f"Should not double-prefix for '{prefix}'"

    def test_from_inbound_empty_message_id(self):
        msg = MagicMock()
        msg.subject = "Test"
        msg.message_id = ""
        msg.references = ["<old@example.com>"]

        ctx = EmailThreadContext.from_inbound(msg)
        assert ctx.in_reply_to is None
        assert ctx.references == ["<old@example.com>"]


class TestEmailSender:
    def test_send_text_new_conversation(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        sender.send_text("Hello!", "user@example.com")
        sender.flush()

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.body == "Hello!"
        assert sent.from_email == "bot@chat.openchatstudio.com"
        assert sent.to == ["user@example.com"]
        assert "Message-ID" in sent.extra_headers

    def test_send_text_threaded_reply(self):
        ctx = EmailThreadContext(
            subject="Re: Help request",
            in_reply_to="<inbound123@example.com>",
            references=["<root@chat.openchatstudio.com>", "<inbound123@example.com>"],
        )
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
            thread_context=ctx,
        )
        sender.send_text("Here's the answer", "user@example.com")
        sender.flush()

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.subject == "Re: Help request"
        assert sent.extra_headers["In-Reply-To"] == "<inbound123@example.com>"
        assert "<root@chat.openchatstudio.com>" in sent.extra_headers["References"]

    def test_last_message_id_captured(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        sender.send_text("Test", "user@example.com")
        sender.flush()

        assert sender.last_message_id is not None
        assert sender.last_message_id.startswith("<")
        assert sender.last_message_id.endswith(">")

    def test_send_text_alone_requires_flush(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
            thread_context=EmailThreadContext(subject="Re: Hi"),
        )

        sender.send_text("Hello", "user@example.com")
        assert len(mail.outbox) == 0  # not sent yet

        sender.flush()
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.body == "Hello"
        assert msg.to == ["user@example.com"]
        assert msg.attachments == []

    @pytest.mark.django_db()
    def test_send_text_then_files_sends_one_combined_email(self, team_with_users):
        team = team_with_users
        file1 = File.create(
            filename="a.pdf",
            file_obj=BytesIO(b"%PDF-A"),
            team_id=team.id,
            purpose="message_media",
            content_type="application/pdf",
        )
        file2 = File.create(
            filename="b.csv",
            file_obj=BytesIO(b"a,b\n1,2"),
            team_id=team.id,
            purpose="message_media",
            content_type="text/csv",
        )

        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
            thread_context=EmailThreadContext(
                subject="Re: docs",
                in_reply_to="<orig@example.com>",
                references=["<orig@example.com>"],
            ),
        )
        sender.send_text("Here are the docs.", "user@example.com")
        sender.send_file(file1, "user@example.com", session_id=1)
        sender.send_file(file2, "user@example.com", session_id=1)
        sender.flush()

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.body == "Here are the docs."
        assert msg.subject == "Re: docs"
        assert msg.extra_headers.get("In-Reply-To") == "<orig@example.com>"
        assert "<orig@example.com>" in msg.extra_headers.get("References", "")
        names = {a[0] for a in msg.attachments}
        assert names == {"a.pdf", "b.csv"}

    def test_flush_with_nothing_queued_is_noop(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        before = len(mail.outbox)
        sender.flush()
        assert len(mail.outbox) == before

    def test_flush_resets_state(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
            thread_context=EmailThreadContext(subject="Re: ad-hoc"),
        )
        before = len(mail.outbox)
        sender.send_text("First", "user@example.com")
        sender.flush()
        sender.send_text("Second", "user@example.com")
        sender.flush()
        assert len(mail.outbox) == before + 2
        assert mail.outbox[before].body == "First"
        assert mail.outbox[before + 1].body == "Second"


class TestEmailChannel:
    def test_capabilities(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com", "from_address": "bot@chat.ocs.com"}
        experiment_mock = MagicMock()

        email_channel = EmailChannel(experiment_mock, channel_mock)
        caps = email_channel._get_capabilities()

        assert caps.supports_voice_replies is False
        assert caps.supports_files is True
        assert caps.supports_conversational_consent is False
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types

    def test_can_send_file_normal_pdf(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        email_channel = EmailChannel(MagicMock(), channel_mock)

        file = MagicMock()
        file.content_type = "application/pdf"
        file.content_size = 1024 * 1024  # 1 MB

        assert email_channel._can_send_file(file) is True

    def test_can_send_file_rejects_oversized(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        email_channel = EmailChannel(MagicMock(), channel_mock)

        file = MagicMock()
        file.content_type = "application/pdf"
        file.content_size = 25 * 1024 * 1024  # 25 MB > 20 MB

        assert email_channel._can_send_file(file) is False

    def test_can_send_file_rejects_denylisted(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        email_channel = EmailChannel(MagicMock(), channel_mock)

        file = MagicMock()
        file.content_type = "application/x-msdownload"
        file.content_size = 1024

        assert email_channel._can_send_file(file) is False

    def test_get_sender_returns_email_sender(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
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
        team = team_with_users
        experiment = ExperimentFactory(team=team)
        ExperimentChannelFactory(
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

        with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            handle_email_message(email_data=email_data)
            MockEmailChannel.assert_called_once()
            mock_instance.new_user_message.assert_called_once()

    def test_no_match_logs_and_returns(self):
        email_data = {
            "participant_id": "sender@example.com",
            "message_text": "Hello",
            "from_address": "sender@example.com",
            "to_address": "unknown@nowhere.com",
            "subject": "Test",
            "message_id": "<msg1@example.com>",
            "in_reply_to": None,
            "references": [],
        }
        # Should not raise
        handle_email_message(email_data=email_data)

    def test_task_uses_channel_id_when_provided(self, team_with_users):
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
            "attachment_file_ids": [],
            "skipped_attachments": [],
        }

        with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            handle_email_message(email_data=email_data, channel_id=channel.id)
            MockEmailChannel.assert_called_once()
            ec_kwarg = MockEmailChannel.call_args.kwargs["experiment_channel"]
            assert ec_kwarg.id == channel.id
            mock_instance.new_user_message.assert_called_once()

    def test_task_legacy_payload_falls_back_to_routing(self, team_with_users):
        """Tasks queued before deploy won't carry channel_id; the task should
        still resolve the channel via the existing routing chain."""
        team = team_with_users
        experiment = ExperimentFactory(team=team)
        ExperimentChannelFactory(
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

        with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            # Call without channel_id (legacy form)
            handle_email_message(email_data=email_data)
            MockEmailChannel.assert_called_once()
            mock_instance.new_user_message.assert_called_once()


@pytest.mark.django_db()
class TestEmailInboundHandler:
    def test_enqueues_task(self, team_with_users):
        team = team_with_users
        ExperimentChannelFactory(
            experiment=ExperimentFactory(team=team),
            platform=ChannelPlatform.EMAIL,
            extra_data={"email_address": "bot@chat.openchatstudio.com"},
            team=team,
        )
        inbound = _make_inbound_message(to_email="bot@chat.openchatstudio.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_called_once()
            call_kwargs = mock_task.delay.call_args[1]
            assert call_kwargs["email_data"]["to_address"] == "bot@chat.openchatstudio.com"

    def test_thread_reply_allowed_through(self, team_with_users):
        """Reply via In-Reply-To is enqueued even when to-address doesn't match a channel."""
        team = team_with_users
        channel = _make_email_channel(team, email_address="bot@chat.openchatstudio.com")
        _make_session(team, channel, "<outbound-1@chat.openchatstudio.com>")

        inbound = _make_inbound_message(
            from_email="user@example.com",  # must match session participant for sender verification
            to_email="different@chat.openchatstudio.com",
            in_reply_to="<outbound-1@chat.openchatstudio.com>",
        )

        with patch("apps.channels.tasks.handle_email_message.delay") as mock_delay:
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_delay.assert_called_once()

    def test_default_channel_allowed_through(self, team_with_users):
        """Email to unknown address is enqueued when any email channel exists."""
        team = team_with_users
        _make_email_channel(team, email_address="bot@chat.openchatstudio.com", is_default=True)

        inbound = _make_inbound_message(to_email="unknown@chat.openchatstudio.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_called_once()

    def test_no_channel_silently_ignored(self):
        """Unmatched email is silently ignored (no bounce loop)."""
        inbound = _make_inbound_message(to_email="unknown@nowhere.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_not_called()

    def test_spam_detected_discarded(self):
        inbound = _make_inbound_message(to_email="bot@chat.openchatstudio.com", spam_detected=True)

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_not_called()

    def test_parse_failure_does_not_raise(self):
        inbound = MagicMock()
        inbound.spam_detected = None
        inbound.from_email.addr_spec = None  # Will cause parse to fail

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, message=inbound, event=MagicMock())
            mock_task.delay.assert_not_called()


class TestEmailSessionThreading:
    def test_sender_captures_message_id(self):
        sender = EmailSender(
            from_address="bot@chat.openchatstudio.com",
            domain="chat.openchatstudio.com",
        )
        sender.send_text("Hello!", "user@example.com")
        sender.flush()

        assert sender.last_message_id is not None
        assert "@chat.openchatstudio.com>" in sender.last_message_id

    def test_new_user_message_updates_session_external_id(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        experiment_mock = MagicMock()
        session_mock = MagicMock()
        session_mock.external_id = "some-uuid-default"

        email_channel = EmailChannel(experiment_mock, channel_mock, session_mock)

        mock_sender = MagicMock(spec=EmailSender)
        mock_sender.last_message_id = "<outbound1@chat.openchatstudio.com>"
        email_channel._sender_instance = mock_sender

        with patch.object(ChannelBase, "new_user_message", return_value=MagicMock()):
            email_channel.new_user_message(MagicMock())

        assert session_mock.external_id == "<outbound1@chat.openchatstudio.com>"
        session_mock.save.assert_called_once_with(update_fields=["external_id"])

    def test_does_not_overwrite_email_message_id(self):
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        experiment_mock = MagicMock()
        session_mock = MagicMock()
        session_mock.external_id = "<already-set@chat.openchatstudio.com>"

        email_channel = EmailChannel(experiment_mock, channel_mock, session_mock)

        mock_sender = MagicMock(spec=EmailSender)
        mock_sender.last_message_id = "<new-id@chat.openchatstudio.com>"
        email_channel._sender_instance = mock_sender

        with patch.object(ChannelBase, "new_user_message", return_value=MagicMock()):
            email_channel.new_user_message(MagicMock())

        assert session_mock.external_id == "<already-set@chat.openchatstudio.com>"
        session_mock.save.assert_not_called()

    def test_external_id_integrity_error_handled(self):
        """IntegrityError when saving external_id is handled gracefully."""
        channel_mock = MagicMock()
        channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
        experiment_mock = MagicMock()
        session_mock = MagicMock()
        session_mock.external_id = "some-uuid-default"
        session_mock.save.side_effect = IntegrityError("duplicate key")

        email_channel = EmailChannel(experiment_mock, channel_mock, session_mock)

        mock_sender = MagicMock(spec=EmailSender)
        mock_sender.last_message_id = "<outbound1@chat.openchatstudio.com>"
        email_channel._sender_instance = mock_sender

        with patch.object(ChannelBase, "new_user_message", return_value=MagicMock()):
            # Should not raise
            result = email_channel.new_user_message(MagicMock())

        assert result is not None
        session_mock.save.assert_called_once_with(update_fields=["external_id"])


@pytest.mark.django_db()
class TestEmailEndToEnd:
    def test_task_routes_new_email_to_channel(self, team_with_users):
        """Task routes a new email to the correct channel via to-address."""
        team = team_with_users
        experiment = ExperimentFactory(team=team)
        ExperimentChannelFactory(
            experiment=experiment,
            platform=ChannelPlatform.EMAIL,
            extra_data={"email_address": "bot@chat.openchatstudio.com"},
            team=team,
        )

        email_data = {
            "participant_id": "user@example.com",
            "message_text": "Can you help me?",
            "from_address": "user@example.com",
            "to_address": "bot@chat.openchatstudio.com",
            "subject": "Need help",
            "message_id": "<user-msg-1@example.com>",
            "in_reply_to": None,
            "references": [],
        }

        with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            handle_email_message(email_data=email_data)

            MockEmailChannel.assert_called_once()
            call_kwargs = MockEmailChannel.call_args[1]
            assert isinstance(call_kwargs["thread_context"], EmailThreadContext)
            assert call_kwargs["thread_context"].subject == "Re: Need help"
            mock_instance.new_user_message.assert_called_once()

    def test_task_routes_reply_to_existing_session(self, team_with_users):
        """Task routes a reply to existing session via In-Reply-To."""
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(
            team, channel, "<outbound-1@chat.openchatstudio.com>", participant_email="user@example.com"
        )

        email_data = {
            "participant_id": "user@example.com",
            "message_text": "Thanks for the info",
            "from_address": "user@example.com",
            "to_address": "bot@chat.openchatstudio.com",
            "subject": "Re: Need help",
            "message_id": "<user-msg-2@example.com>",
            "in_reply_to": "<outbound-1@chat.openchatstudio.com>",
            "references": ["<outbound-1@chat.openchatstudio.com>"],
        }

        with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
            mock_instance = MockEmailChannel.return_value
            handle_email_message(email_data=email_data)

            MockEmailChannel.assert_called_once()
            call_kwargs = MockEmailChannel.call_args[1]
            assert call_kwargs["experiment_session"] == session
            mock_instance.new_user_message.assert_called_once()


@pytest.mark.django_db()
class TestPersistInboundAttachments:
    def _raw(self, filename, content_type, content):
        return RawAttachment(filename=filename, content_type=content_type, content_bytes=content)

    def test_accepts_normal_file(self):
        team = TeamFactory()
        raw = [self._raw("data.csv", "text/csv", b"a,b\n1,2\n")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 1
        assert skipped == []
        f = File.objects.get(id=accepted[0])
        assert f.team_id == team.id
        assert f.name == "data.csv"
        assert f.purpose == "message_media"

    def test_rejects_oversized(self):
        team = TeamFactory()
        big = b"x" * (21 * 1024 * 1024)
        raw = [self._raw("big.pdf", "application/pdf", big)]

        # Mock magic detection so the content-type checks pass and only the
        # size check fires (magic sees b"x"*N as text/plain, causing a
        # spurious mismatch against application/pdf before the size check).
        with patch(
            "apps.channels.channels_v2.email_channel._detect_content_type",
            return_value="application/pdf",
        ):
            accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert len(skipped) == 1
        assert "20" in skipped[0]["reason"]
        assert skipped[0]["size"] == len(big)

    def test_rejects_denylisted_extension(self):
        team = TeamFactory()
        raw = [self._raw("malware.exe", "application/octet-stream", b"MZ\x90\x00")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert len(skipped) == 1
        assert ".exe" in skipped[0]["reason"]

    def test_rejects_denylisted_content_type(self):
        team = TeamFactory()
        raw = [self._raw("noext", "application/x-msdownload", b"\x00\x00")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert "application/x-msdownload" in skipped[0]["reason"]

    def test_rejects_when_magic_detects_executable_with_innocent_filename(self):
        """When magic detects a denylisted type, the attachment is rejected
        regardless of the (possibly spoofed) filename and claimed type."""
        team = TeamFactory()
        raw = [self._raw("report.pdf", "application/pdf", b"any bytes")]

        # Force magic detection to return an executable type so we test the
        # detection-based rejection branch without relying on real libmagic
        # signatures (which can vary by version/platform).
        with patch(
            "apps.channels.channels_v2.email_channel._detect_content_type",
            return_value="application/x-msdownload",
        ):
            accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert "detected" in skipped[0]["reason"].lower()
        assert "application/x-msdownload" in skipped[0]["reason"]

    def test_canonical_content_type_is_magic_detected(self):
        team = TeamFactory()
        # PNG magic bytes; sender claims image/png — magic should agree
        png_bytes = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 32)
        raw = [self._raw("image.png", "image/png", png_bytes)]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 1
        f = File.objects.get(id=accepted[0])
        assert f.content_type.startswith("image/png")

    def test_storage_error_isolated(self):
        team = TeamFactory()
        raw = [
            self._raw("a.txt", "text/plain", b"hello"),
            self._raw("b.txt", "text/plain", b"world"),
            self._raw("c.txt", "text/plain", b"again"),
        ]
        original = File.create
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated storage error")
            return original(*args, **kwargs)

        with patch.object(File, "create", side_effect=flaky):
            accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 2
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "storage error"
        assert skipped[0]["name"] == "b.txt"

    @pytest.mark.parametrize(
        ("ext", "claimed", "detected", "should_block"),
        [
            ("pdf", "image/jpeg", "application/pdf", True),  # cross-category mismatch
            ("pdf", "application/octet-stream", "application/pdf", False),  # claimed unknown
            ("pdf", "application/pdf", "application/octet-stream", False),  # detected unknown
            ("json", "application/json", "text/plain", False),  # text-like allowlist
            ("xml", "application/xml", "text/plain", False),
            ("csv", "text/csv", "application/javascript", True),  # script not allowlisted
            ("csv", "text/csv", "text/plain", False),  # same text category
        ],
    )
    def test_is_blocked_mismatch_matrix(self, ext, claimed, detected, should_block):
        result = _is_blocked(ext, claimed, detected)
        if should_block:
            assert result is not None
            assert "mismatch" in result.lower() or "not allowed" in result.lower()
        else:
            assert result is None


@pytest.mark.django_db()
class TestEmailInboundHandlerWithAttachments:
    def test_handler_persists_files_before_enqueue(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-...")
        inbound = _make_inbound_with_attachments([pdf], to_email=channel.extra_data["email_address"])

        with patch("apps.channels.tasks.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        assert File.objects.filter(team_id=team.id, name="report.pdf").count() == 1
        delay.assert_called_once()
        kwargs = delay.call_args.kwargs
        assert kwargs["channel_id"] == channel.id
        assert len(kwargs["email_data"]["attachment_file_ids"]) == 1

    def test_handler_no_files_saved_when_no_channel_match(self, team_with_users):
        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-")
        inbound = _make_inbound_with_attachments([pdf], to_email="nobody@example.com")

        with patch("apps.channels.tasks.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        assert File.objects.count() == 0
        delay.assert_not_called()

    def test_skipped_attachments_appended_to_message_text(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        big = b"x" * (21 * 1024 * 1024)
        oversized = _mime_part(filename="huge.pdf", content_type="application/pdf", content=big)
        inbound = _make_inbound_with_attachments(
            [oversized], to_email=channel.extra_data["email_address"], text="Please process"
        )

        # Mock _detect_content_type so the oversized PDF doesn't trip the
        # mismatch check (libmagic sees a long string of "x" as text/plain).
        with (
            patch("apps.channels.channels_v2.email_channel._detect_content_type", return_value="application/pdf"),
            patch("apps.channels.tasks.handle_email_message.delay") as delay,
        ):
            email_inbound_handler(sender=None, message=inbound, event=None)

        delay.assert_called_once()
        message_text = delay.call_args.kwargs["email_data"]["message_text"]
        assert "Please process" in message_text
        assert "huge.pdf" in message_text
        assert "skipped" in message_text.lower()
        assert File.objects.filter(team_id=team.id).count() == 0

    def test_handler_passes_session_id_when_thread_continuation(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(team, channel, external_id="<thread-anchor@example.com>")
        inbound = _make_inbound_message(
            to_email=channel.extra_data["email_address"],
            in_reply_to="<thread-anchor@example.com>",
            from_email="user@example.com",
        )
        inbound.attachments = []

        with patch("apps.channels.tasks.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        delay.assert_called_once()
        assert delay.call_args.kwargs["session_id"] == session.id
