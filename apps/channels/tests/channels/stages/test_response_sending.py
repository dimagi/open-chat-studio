from unittest.mock import MagicMock

from apps.channels.channels_v2.stages.terminal import ResponseSendingStage
from apps.channels.tests.channels.conftest import StubSender, make_context


class TestResponseSendingStage:
    def setup_method(self):
        self.stage = ResponseSendingStage()

    def test_should_not_run_when_nothing_to_send(self):
        ctx = make_context(formatted_message=None, early_exit_response=None)
        assert self.stage.should_run(ctx) is False

    def test_sends_early_exit_as_text(self):
        sender = StubSender()
        ctx = make_context(
            sender=sender,
            early_exit_response="Sorry, not allowed",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert len(sender.text_messages) == 1
        assert sender.text_messages[0] == ("Sorry, not allowed", "user1")

    def test_sends_text_response(self):
        sender = StubSender()
        ctx = make_context(
            sender=sender,
            formatted_message="Bot response here",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert len(sender.text_messages) == 1
        assert sender.text_messages[0] == ("Bot response here", "user1")

    def test_sends_voice_response(self):
        sender = StubSender()
        voice_audio = MagicMock()
        ctx = make_context(
            sender=sender,
            formatted_message="Bot response here",
            voice_audio=voice_audio,
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert len(sender.voice_messages) == 1
        assert sender.voice_messages[0] == (voice_audio, "user1")

    def test_sends_files(self):
        sender = StubSender()
        file1 = MagicMock()
        file2 = MagicMock()
        session = MagicMock()
        session.id = 42
        ctx = make_context(
            sender=sender,
            formatted_message="Here are files",
            participant_identifier="user1",
            experiment_session=session,
            files_to_send=[file1, file2],
        )

        self.stage(ctx)

        assert len(sender.files_sent) == 2
        assert sender.files_sent[0] == (file1, "user1", 42)
        assert sender.files_sent[1] == (file2, "user1", 42)

    def test_send_failure_sets_sending_exception(self):
        sender = MagicMock()
        error = RuntimeError("send failed")
        sender.send_text.side_effect = error
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert ctx.sending_exception is error
        assert any("Send failed" in e for e in ctx.processing_errors)

    def test_file_send_failure_sends_download_link(self):
        sender = StubSender()
        # Override send_file to fail
        file1 = MagicMock()
        file1.content_type = "image/png"
        file1.download_link.return_value = "https://example.com/download"

        sender.send_file = MagicMock(side_effect=RuntimeError("file send failed"))  # type: ignore[assignment]
        session = MagicMock()
        session.id = 42
        experiment_channel = MagicMock()
        experiment_channel.platform_enum.title.return_value = "Telegram"
        ctx = make_context(
            sender=sender,
            formatted_message="Here is a file",
            participant_identifier="user1",
            experiment_session=session,
            experiment_channel=experiment_channel,
            files_to_send=[file1],
        )

        self.stage(ctx)

        # The download link should have been sent as text
        assert any("https://example.com/download" in text for text, _ in sender.text_messages)
