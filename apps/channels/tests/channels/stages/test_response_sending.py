from unittest.mock import MagicMock

from apps.channels.stages.terminal import FileDeliveryFailure, MessageDeliveryFailure, ResponseSendingStage
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

    def test_send_failure_appends_to_sending_exceptions(self):
        sender = MagicMock()
        error = RuntimeError("send failed")
        sender.send_text.side_effect = error
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert len(ctx.sending_exceptions) == 1
        assert isinstance(ctx.sending_exceptions[0], MessageDeliveryFailure)
        assert ctx.sending_exceptions[0].original_exc is error
        assert any("Send failed" in e for e in ctx.processing_errors)

    def test_file_send_failure_stores_exception_and_sends_download_link(self):
        sender = StubSender()
        file1 = MagicMock()
        file1.content_type = "image/png"
        file1.download_link.return_value = "https://example.com/download"
        error = RuntimeError("file send failed")
        sender.send_file = MagicMock(side_effect=error)
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

        assert len(ctx.sending_exceptions) == 1
        exc = ctx.sending_exceptions[0]
        assert isinstance(exc, FileDeliveryFailure)
        assert exc.original_exc is error
        assert exc.file is file1
        # Download link sent as fallback
        assert any("https://example.com/download" in text for text, _ in sender.text_messages)

    def test_text_send_failure_skips_file_sending(self):
        sender = MagicMock()
        error = RuntimeError("send failed")
        sender.send_text.side_effect = error
        file1 = MagicMock()
        session = MagicMock()
        session.id = 42
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
            experiment_session=session,
            files_to_send=[file1],
        )

        self.stage(ctx)

        assert len(ctx.sending_exceptions) == 1
        assert isinstance(ctx.sending_exceptions[0], MessageDeliveryFailure)
        sender.send_file.assert_not_called()

    def test_response_sending_stage_calls_flush(self):
        sender = StubSender()
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert sender.flush_call_count == 1

    def test_flush_is_called_after_files(self):
        sender = StubSender()
        file1 = MagicMock()
        session = MagicMock()
        session.id = 42
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
            experiment_session=session,
            files_to_send=[file1],
        )

        self.stage(ctx)

        # flush must be called once, after both text and files were sent
        assert sender.flush_call_count == 1
        assert sender.call_order == ["send_text", "send_file", "flush"]

    def test_flush_failure_recorded_as_message_delivery_failure(self):
        sender = StubSender()
        error = RuntimeError("flush failed")
        sender.flush_side_effect = error
        ctx = make_context(
            sender=sender,
            formatted_message="Hello",
            participant_identifier="user1",
        )

        self.stage(ctx)

        assert len(ctx.sending_exceptions) == 1
        exc = ctx.sending_exceptions[0]
        assert isinstance(exc, MessageDeliveryFailure)
        assert exc.original_exc is error
        assert exc.context == "flush"


class TestVoiceToTextFallback:
    """A channel can pass should_voice_fallback_to_text into the stage. When voice delivery
    fails and the predicate approves the exception, the full formatted message is sent as
    text instead and no delivery failure is recorded."""

    def _voice_ctx(self, sender, **kwargs):
        kwargs.setdefault("formatted_message", "full reply text")
        return make_context(
            sender=sender,
            voice_audio=MagicMock(),
            participant_identifier="user1",
            **kwargs,
        )

    def _failing_voice_sender(self, error):
        sender = MagicMock()
        sender.send_voice.side_effect = error
        return sender

    def test_falls_back_to_text_when_predicate_approves(self):
        error = RuntimeError("window expired")
        sender = self._failing_voice_sender(error)
        stage = ResponseSendingStage(should_voice_fallback_to_text=lambda exc: exc is error)
        ctx = self._voice_ctx(sender)

        stage(ctx)

        sender.send_text.assert_called_once_with("full reply text", "user1")
        assert ctx.sending_exceptions == []
        # Response is no longer voice: persistence must not tag it or attach audio
        assert ctx.voice_audio is None

    def test_fallback_does_not_send_additional_text_message(self):
        """The URLs split into additional_text_message for the voice path are still inline
        in formatted_message (it's captured before URL-stripping), so the fallback delivers
        them as part of the full text; sending the follow-up too would duplicate them."""
        error = RuntimeError("window expired")
        sender = self._failing_voice_sender(error)
        stage = ResponseSendingStage(should_voice_fallback_to_text=lambda exc: True)
        ctx = self._voice_ctx(
            sender,
            formatted_message="full reply text https://example.com",
            additional_text_message="https://example.com",
        )

        stage(ctx)

        sender.send_text.assert_called_once_with("full reply text https://example.com", "user1")

    def test_voice_failure_raises_when_predicate_declines(self):
        error = RuntimeError("some other failure")
        sender = self._failing_voice_sender(error)
        stage = ResponseSendingStage(should_voice_fallback_to_text=lambda exc: False)
        ctx = self._voice_ctx(sender)

        stage(ctx)

        sender.send_text.assert_not_called()
        assert len(ctx.sending_exceptions) == 1
        exc = ctx.sending_exceptions[0]
        assert isinstance(exc, MessageDeliveryFailure)
        assert exc.original_exc is error
        assert exc.context == "voice message"

    def test_voice_failure_raises_by_default(self):
        """Without a predicate the stage keeps its original behavior."""
        error = RuntimeError("voice failed")
        sender = self._failing_voice_sender(error)
        stage = ResponseSendingStage()
        ctx = self._voice_ctx(sender)

        stage(ctx)

        sender.send_text.assert_not_called()
        assert len(ctx.sending_exceptions) == 1
        assert ctx.sending_exceptions[0].context == "voice message"

    def test_fallback_text_failure_recorded_as_text_delivery_failure(self):
        """If the text retry also fails, the team still gets notified via the
        generic path -- about the text failure that ultimately blocked the reply."""
        voice_error = RuntimeError("window expired")
        text_error = RuntimeError("no template")
        sender = self._failing_voice_sender(voice_error)
        sender.send_text.side_effect = text_error
        stage = ResponseSendingStage(should_voice_fallback_to_text=lambda exc: True)
        ctx = self._voice_ctx(sender)

        stage(ctx)

        assert len(ctx.sending_exceptions) == 1
        exc = ctx.sending_exceptions[0]
        assert isinstance(exc, MessageDeliveryFailure)
        assert exc.original_exc is text_error
        assert exc.context == "text message"

    def test_no_fallback_without_formatted_message(self):
        """Nothing to retry with -> the voice failure is recorded as usual."""
        error = RuntimeError("window expired")
        sender = self._failing_voice_sender(error)
        stage = ResponseSendingStage(should_voice_fallback_to_text=lambda exc: True)
        ctx = make_context(
            sender=sender,
            formatted_message=None,
            early_exit_response=None,
            voice_audio=MagicMock(),
            participant_identifier="user1",
        )
        # should_run is False without formatted_message; drive process directly to
        # pin the guard inside the voice path as well
        stage.process(ctx)

        sender.send_text.assert_not_called()
        assert len(ctx.sending_exceptions) == 1
        assert ctx.sending_exceptions[0].context == "voice message"
