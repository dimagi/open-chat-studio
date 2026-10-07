"""
Tests for the v2 CommCare Connect channel implementation:
  - attachment naming and type helpers (unit)
  - CommCareConnectSender (unit; one DB-backed test for key generation)
  - CommCareConnectChannel full pipeline (integration)

Generic platform-consent behavior is tested in test_consent_config_stage.py.
"""

import base64
import logging
import os
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import httpx
import pytest
from django.test import override_settings
from waffle.testutils import override_flag

from apps.channels.clients.connect_client import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    CommCareConnectClient,
    Message,
    NewMessagePayload,
    OutgoingAttachment,
)
from apps.channels.connect_channel import (
    CommCareConnectChannel,
    CommCareConnectSender,
    attachment_mime_type,
    unique_attachment_name,
)
from apps.channels.models import ChannelPlatform
from apps.channels.pipeline import MessageProcessingContext
from apps.channels.stages.terminal import FileDeliveryFailure, ResponseSendingStage
from apps.channels.tasks import handle_commcare_connect_message
from apps.channels.tests._file_helpers import make_mock_file
from apps.channels.tests.channels.conftest import make_context
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ParticipantData
from apps.teams.flags import Flags
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ParticipantFactory

# Largest file that fits PersonalID's per-attachment limit once the 28 bytes of encryption are added
_LARGEST_ATTACHMENT = MAX_ATTACHMENT_BYTES - 28

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_participant_data(experiment, consent=True):
    """Create a participant + ParticipantData with optional commcare metadata."""
    connect_id = str(uuid4())
    channel_id = str(uuid4())
    encryption_key = os.urandom(32)
    participant = ParticipantFactory.create(
        identifier=connect_id, team=experiment.team, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    participant_data = ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata={"commcare_connect_channel_id": channel_id, "consent": consent},
        encryption_key=base64.b64encode(encryption_key).decode("utf-8"),
    )
    return participant, participant_data, channel_id, encryption_key


def _encrypt_user_message(encryption_key, channel_id, text="Hello bot"):
    """Build an encrypted NewMessagePayload as CommCare Connect would send it."""
    client = CommCareConnectClient()
    ciphertext, tag, nonce = client._encrypt_message(encryption_key=encryption_key, message=text)
    message = Message(timestamp=1000, message_id=str(uuid4()), ciphertext=ciphertext, tag=tag, nonce=nonce)
    return NewMessagePayload(channel_id=channel_id, messages=[message])


_CHANNEL_ID = "connect-channel-1"
_ENCRYPTION_KEY = b"k" * 32


def _mock_participant_data(system_metadata=None):
    participant_data = Mock(spec=ParticipantData)
    participant_data.system_metadata = (
        {"commcare_connect_channel_id": _CHANNEL_ID} if system_metadata is None else system_metadata
    )
    participant_data.encryption_key = base64.b64encode(_ENCRYPTION_KEY).decode()
    participant_data.get_encryption_key_bytes.return_value = _ENCRYPTION_KEY
    return participant_data


def _bound_sender(participant_data=None, send_fcm_fallback=False):
    sender = CommCareConnectSender(send_fcm_fallback=send_fcm_fallback)
    participant_data = _mock_participant_data() if participant_data is None else participant_data
    sender.bind(Mock(spec=MessageProcessingContext, participant_data=participant_data))
    return sender


def _file(name, content=b"filedata", content_type="application/pdf"):
    return make_mock_file(name, content_type, len(content), content)


@pytest.fixture()
def client_class():
    with patch("apps.channels.connect_channel.CommCareConnectClient") as client_class:
        yield client_class


@pytest.fixture()
def create_message_flag():
    with override_flag(Flags.COMMCARE_CONNECT_CREATE_MESSAGE.slug, active=True):
        yield


# ---------------------------------------------------------------------------
# Attachment naming and type helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "taken", "expected"),
    [
        pytest.param("report.pdf", [], "report.pdf", id="unused"),
        pytest.param("report.pdf", ["report.pdf"], "report (2).pdf", id="duplicate"),
        pytest.param("Report.PDF", ["report.pdf"], "Report (2).PDF", id="duplicate-ignoring-case"),
        pytest.param("report.pdf", ["report.pdf", "report (2).pdf"], "report (3).pdf", id="third-copy"),
        pytest.param("notes", ["notes"], "notes (2)", id="no-extension"),
        pytest.param(".env", [".env"], ".env (2)", id="dotfile"),
        pytest.param("archive.tar.gz", ["archive.tar.gz"], "archive.tar (2).gz", id="multiple-dots"),
        pytest.param("報告.pdf", ["報告.pdf"], "報告 (2).pdf", id="non-ascii"),
        pytest.param("reports/2026/summary.pdf", [], "summary.pdf", id="posix-path"),
        pytest.param("C:\\Users\\me\\summary.pdf", [], "summary.pdf", id="windows-path"),
        pytest.param("", [], "attachment", id="empty"),
        pytest.param("..", [], "attachment", id="parent-directory"),
        pytest.param("a" * 300 + ".pdf", [], "a" * 251 + ".pdf", id="long-name-shortened"),
        pytest.param("a" * 251 + ".pdf", ["a" * 251 + ".pdf"], "a" * 247 + " (2).pdf", id="long-duplicate"),
        pytest.param("a." + "b" * 300, [], ("a." + "b" * 300)[:255], id="long-extension-is-not-one"),
    ],
)
def test_unique_attachment_name(name, taken, expected):
    assert unique_attachment_name(name, taken) == expected


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        pytest.param("application/pdf", "application/pdf", id="plain"),
        pytest.param("text/plain; charset=utf-8", "text/plain", id="parameters"),
        pytest.param("Image/JPEG", "image/jpeg", id="upper-case"),
        pytest.param("", "application/octet-stream", id="empty"),
        pytest.param(None, "application/octet-stream", id="none"),
    ],
)
def test_attachment_mime_type(content_type, expected):
    assert attachment_mime_type(content_type) == expected


# ---------------------------------------------------------------------------
# CommCareConnectSender
# ---------------------------------------------------------------------------


class TestCommCareConnectSender:
    def test_text_and_files_sent_as_one_message(self, client_class):
        sender = _bound_sender()

        sender.send_text("See attached", recipient="participant")
        sender.send_file(_file("a.pdf", b"aaa"), "participant", session_id=1)
        sender.send_file(_file("b.png", b"bbb", content_type="image/png"), "participant", session_id=1)
        sender.flush()

        client_class.return_value.create_message.assert_called_once_with(
            channel_id=_CHANNEL_ID,
            encryption_key=_ENCRYPTION_KEY,
            text="See attached",
            attachments=[
                OutgoingAttachment(name="a.pdf", content_type="application/pdf", content=b"aaa"),
                OutgoingAttachment(name="b.png", content_type="image/png", content=b"bbb"),
            ],
        )

    def test_text_only_message(self, client_class):
        sender = _bound_sender()

        sender.send_text("Hello world", recipient="participant")
        sender.flush()

        client_class.return_value.create_message.assert_called_once_with(
            channel_id=_CHANNEL_ID, encryption_key=_ENCRYPTION_KEY, text="Hello world", attachments=[]
        )

    def test_texts_are_joined_with_a_blank_line(self, client_class):
        sender = _bound_sender()

        sender.send_text("First", recipient="participant")
        sender.send_text("Second", recipient="participant")
        sender.flush()

        assert client_class.return_value.create_message.call_args.kwargs["text"] == "First\n\nSecond"

    def test_flush_with_nothing_buffered_sends_nothing(self, client_class):
        _bound_sender().flush()

        client_class.assert_not_called()

    def test_buffer_resets_after_a_send(self, client_class):
        sender = _bound_sender()

        sender.send_text("One", recipient="participant")
        sender.send_file(_file("a.pdf"), "participant", session_id=1)
        sender.flush()
        sender.send_text("Two", recipient="participant")
        sender.flush()

        second = client_class.return_value.create_message.call_args_list[1].kwargs
        assert second["text"] == "Two"
        assert second["attachments"] == []

    def test_buffer_resets_after_a_failed_send(self, client_class):
        client_class.return_value.create_message.side_effect = [httpx.ConnectError("unreachable"), None]
        sender = _bound_sender()

        sender.send_text("One", recipient="participant")
        with pytest.raises(httpx.ConnectError):
            sender.flush()
        sender.send_text("Two", recipient="participant")
        sender.flush()

        assert client_class.return_value.create_message.call_args.kwargs["text"] == "Two"

    def test_attachment_names_are_made_unique(self, client_class):
        sender = _bound_sender()

        for name in ("report.pdf", "Report.PDF", "report.pdf"):
            sender.send_file(_file(name), "participant", session_id=1)
        sender.flush()

        attachments = client_class.return_value.create_message.call_args.kwargs["attachments"]
        assert [attachment.name for attachment in attachments] == ["report.pdf", "Report (2).PDF", "report (3).pdf"]

    def test_attachment_type_is_bare_mime_type(self, client_class):
        sender = _bound_sender()

        sender.send_file(_file("notes.txt", content_type="Text/Plain; charset=utf-8"), "participant", session_id=1)
        sender.flush()

        attachments = client_class.return_value.create_message.call_args.kwargs["attachments"]
        assert attachments[0].content_type == "text/plain"

    @pytest.mark.parametrize(
        ("participant_data", "error"),
        [
            pytest.param(None, "Participant data not found", id="no-participant-data"),
            pytest.param(_mock_participant_data(system_metadata={}), "channel_id is missing", id="no-channel-id"),
        ],
    )
    def test_flush_raises_without_a_channel(self, client_class, participant_data, error):
        sender = CommCareConnectSender()
        sender.bind(Mock(spec=MessageProcessingContext, participant_data=participant_data))
        sender.send_text("Hi", recipient="ghost-participant")

        with pytest.raises(ChannelException, match=error):
            sender.flush()

    @pytest.mark.django_db()
    def test_missing_encryption_key_is_generated(self, client_class, experiment):
        """When the encryption key is missing, the sender generates one and
        proceeds. The mobile app always calls ``get_key`` before decrypting,
        so it will read whatever key we used to encrypt the message."""
        participant, participant_data, _channel_id, _ = _make_participant_data(experiment, consent=True)
        participant_data.encryption_key = ""
        participant_data.save()
        sender = _bound_sender(participant_data=participant_data)

        sender.send_text("Hello", recipient=participant.identifier)
        sender.flush()

        participant_data.refresh_from_db()
        assert participant_data.encryption_key
        call = client_class.return_value.create_message.call_args.kwargs
        assert call["encryption_key"] == participant_data.get_encryption_key_bytes()


class TestCommCareConnectSenderLimits:
    """Files PersonalID cannot take in this message are sent as download links in its text."""

    def test_file_after_the_tenth_is_a_link_and_is_not_read(self, client_class):
        sender = _bound_sender()
        files = [_file(f"file{index}.pdf") for index in range(MAX_ATTACHMENTS_PER_MESSAGE + 1)]

        sender.send_text("Files", recipient="participant")
        for file in files:
            sender.send_file(file, "participant", session_id=1)
        sender.flush()

        call = client_class.return_value.create_message.call_args.kwargs
        assert len(call["attachments"]) == MAX_ATTACHMENTS_PER_MESSAGE
        assert call["text"] == "Files\n\nfile10.pdf\nhttp://example.com/file10.pdf"
        files[-1].read_bytes.assert_not_called()

    def test_message_total_fits_exactly_at_the_limit(self, client_class):
        sender = _bound_sender()
        content = b"x" * _LARGEST_ATTACHMENT

        for index in range(6):  # 6 x 2.5 MiB encrypted = 15 MiB
            sender.send_file(_file(f"big{index}.bin", content), "participant", session_id=1)
        sender.send_file(_file("tiny.txt", b"x"), "participant", session_id=1)
        sender.flush()

        call = client_class.return_value.create_message.call_args.kwargs
        assert [attachment.name for attachment in call["attachments"]] == [f"big{index}.bin" for index in range(6)]
        assert call["text"] == "tiny.txt\nhttp://example.com/tiny.txt"

    def test_file_over_the_message_total_is_a_link_and_a_later_smaller_file_still_fits(self, client_class, caplog):
        sender = _bound_sender()
        large = b"x" * 2_600_000

        for index in range(6):  # 15,600,168 bytes encrypted, leaving 128,472
            sender.send_file(_file(f"big{index}.bin", large), "participant", session_id=1)
        with caplog.at_level(logging.WARNING, logger="ocs.channels"):
            sender.send_file(_file("medium.bin", b"x" * 200_000), "participant", session_id=1)
        sender.send_file(_file("small.bin", b"x" * 100_000), "participant", session_id=1)
        sender.flush()

        call = client_class.return_value.create_message.call_args.kwargs
        assert [attachment.name for attachment in call["attachments"]][-1] == "small.bin"
        assert len(call["attachments"]) == 7
        assert call["text"] == "medium.bin\nhttp://example.com/medium.bin"
        assert "download link" in caplog.text

    def test_file_larger_than_its_recorded_size_is_a_link(self, client_class):
        sender = _bound_sender()
        file = make_mock_file("big.bin", "application/octet-stream", 10, b"x" * (_LARGEST_ATTACHMENT + 1))

        sender.send_file(file, "participant", session_id=1)
        sender.flush()

        call = client_class.return_value.create_message.call_args.kwargs
        assert call["attachments"] == []
        assert call["text"] == "big.bin\nhttp://example.com/big.bin"

    def test_empty_file_is_a_link(self, client_class):
        sender = _bound_sender()

        sender.send_file(_file("empty.txt", b""), "participant", session_id=1)
        sender.flush()

        call = client_class.return_value.create_message.call_args.kwargs
        assert call["attachments"] == []
        assert call["text"] == "empty.txt\nhttp://example.com/empty.txt"

    def test_unreadable_file_becomes_a_link_and_the_rest_of_the_reply_is_sent(self, client_class):
        """Run through ResponseSendingStage: a storage error on one file falls back to its link
        without losing the text or the other attachments."""
        unreadable = _file("b.pdf")
        unreadable.read_bytes.side_effect = OSError("storage unavailable")
        ctx = make_context(
            sender=CommCareConnectSender(),
            participant_data=_mock_participant_data(),
            experiment_session=MagicMock(id=1),
            formatted_message="Here you go",
            files_to_send=[unreadable, _file("a.pdf", b"aaa")],
        )

        ResponseSendingStage().process(ctx)

        assert [type(exc) for exc in ctx.sending_exceptions] == [FileDeliveryFailure]
        call = client_class.return_value.create_message.call_args.kwargs
        assert call["text"] == "Here you go\n\nhttp://example.com/b.pdf"
        assert [attachment.name for attachment in call["attachments"]] == ["a.pdf"]


class TestCommCareConnectSenderFallback:
    def test_sends_text_through_send_fcm_with_files_as_links(self, client_class):
        sender = _bound_sender(send_fcm_fallback=True)
        file = _file("a.pdf")

        sender.send_text("Hi", recipient="participant")
        sender.send_file(file, "participant", session_id=1)
        sender.flush()

        client_class.return_value.send_message_to_user.assert_called_once_with(
            channel_id=_CHANNEL_ID, message="Hi\n\na.pdf\nhttp://example.com/a.pdf", encryption_key=_ENCRYPTION_KEY
        )
        client_class.return_value.create_message.assert_not_called()
        file.read_bytes.assert_not_called()


# ---------------------------------------------------------------------------
# CommCareConnectChannel
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCommCareConnectChannelCreateMessageFlag:
    def _channel(self, experiment):
        experiment_channel = ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        return CommCareConnectChannel(experiment, experiment_channel)

    @pytest.mark.parametrize("create_message", [pytest.param(False, id="flag-off"), pytest.param(True, id="flag-on")])
    def test_capabilities_follow_the_flag(self, experiment, create_message):
        with override_flag(Flags.COMMCARE_CONNECT_CREATE_MESSAGE.slug, active=create_message):
            capabilities = self._channel(experiment)._get_capabilities()

        assert capabilities.supports_files is create_message

    def test_flag_is_read_once_for_the_sender_and_capabilities(self, experiment):
        channel = self._channel(experiment)

        with patch("apps.channels.connect_channel.flag_is_active_for_team", return_value=False) as flag_check:
            capabilities = channel._get_capabilities()
            sender = channel._get_sender()

        flag_check.assert_called_once_with(experiment.team, Flags.COMMCARE_CONNECT_CREATE_MESSAGE.slug)
        assert capabilities.supports_files is False
        assert sender.send_fcm_fallback is True


@pytest.mark.django_db()
@pytest.mark.usefixtures("create_message_flag")
class TestCommCareConnectChannelAdHocFiles:
    """Ad hoc sends (reminders, trigger-bot) run the formatting and sending stages too."""

    def _channel_with_session(self, experiment):
        participant, _participant_data, channel_id, encryption_key = _make_participant_data(experiment)
        experiment_channel = ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        session = CommCareConnectChannel.start_new_session(experiment, experiment_channel, participant.identifier)
        channel = CommCareConnectChannel(experiment, experiment_channel, experiment_session=session)
        return channel, channel_id, encryption_key

    def test_file_sent_as_attachment(self, experiment, client_class):
        channel, channel_id, encryption_key = self._channel_with_session(experiment)

        channel.send_message_to_user("Here's your file", files=[_file("document.pdf", b"filedata")])

        client_class.return_value.create_message.assert_called_once_with(
            channel_id=channel_id,
            encryption_key=encryption_key,
            text="Here's your file",
            attachments=[OutgoingAttachment(name="document.pdf", content_type="application/pdf", content=b"filedata")],
        )

    def test_flag_off_sends_file_as_link_through_send_fcm(self, experiment, client_class):
        channel, _channel_id, _encryption_key = self._channel_with_session(experiment)

        with override_flag(Flags.COMMCARE_CONNECT_CREATE_MESSAGE.slug, active=False):
            channel.send_message_to_user("Here's your file", files=[_file("document.pdf")])

        client_class.return_value.create_message.assert_not_called()
        message = client_class.return_value.send_message_to_user.call_args.kwargs["message"]
        assert "document.pdf\nhttp://example.com/document.pdf" in message

    def test_file_over_the_attachment_limit_becomes_a_link_before_sending(self, experiment, client_class):
        channel, _channel_id, _encryption_key = self._channel_with_session(experiment)
        file = make_mock_file("big.bin", "application/octet-stream", _LARGEST_ATTACHMENT + 1)

        channel.send_message_to_user("Here's your file", files=[file])

        call = client_class.return_value.create_message.call_args.kwargs
        assert call["attachments"] == []
        assert "big.bin\nhttp://example.com/big.bin" in call["text"]
        file.read_bytes.assert_not_called()


# ---------------------------------------------------------------------------
# CommCareConnectChannel full pipeline integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
@pytest.mark.usefixtures("create_message_flag")
class TestCommCareConnectChannelIntegration:
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_bot_generates_and_sends_encrypted_message(self, experiment):
        """Full pipeline: task decrypts message, bot responds, sender encrypts and sends."""
        _participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=True)
        ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input") as mock_bot,
            patch("apps.channels.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            mock_bot.return_value = ChatMessage(content="Hi human", message_type=ChatMessageType.AI)
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        client_instance = ClientMock.return_value
        assert client_instance.create_message.call_count == 1
        call_kwargs = client_instance.create_message.call_args.kwargs
        assert call_kwargs["channel_id"] == channel_id
        assert call_kwargs["text"] == "Hi human"
        assert call_kwargs["encryption_key"] == encryption_key

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_pipeline_aborts_silently_when_consent_revoked(self, experiment):
        """Pipeline aborts at consent check: bot is never invoked AND no
        message is sent back to the participant."""
        _participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=False)
        ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input") as mock_bot,
            patch("apps.channels.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        mock_bot.assert_not_called()
        ClientMock.assert_not_called()

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_disabled_channel_delivers_its_static_message(self, experiment):
        """The sender encrypts against ParticipantData, so the disabled check has to run
        after the participant stages -- otherwise the message can never be delivered."""
        _participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=True)
        ExperimentChannelFactory.create(
            team=experiment.team,
            experiment=experiment,
            platform=ChannelPlatform.COMMCARE_CONNECT,
            enabled=False,
            disabled_message="This bot is paused",
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input") as mock_bot,
            patch("apps.channels.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        mock_bot.assert_not_called()
        call_kwargs = ClientMock.return_value.create_message.call_args.kwargs
        assert call_kwargs["channel_id"] == channel_id
        assert call_kwargs["text"] == "This bot is paused"

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_disabled_channel_still_honours_revoked_consent(self, experiment):
        """Being disabled is not licence to message someone who has withdrawn consent."""
        _participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=False)
        ExperimentChannelFactory.create(
            team=experiment.team,
            experiment=experiment,
            platform=ChannelPlatform.COMMCARE_CONNECT,
            enabled=False,
            disabled_message="This bot is paused",
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input"),
            patch("apps.channels.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        ClientMock.assert_not_called()
