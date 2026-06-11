from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.channels_v2.stages.core import ConsentFlowStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.experiments.models import SessionStatus


class TestConsentFlowStage:
    def setup_method(self):
        self.stage = ConsentFlowStage()

    def _make_session(self, status=SessionStatus.SETUP, first_human_message=None):
        session = MagicMock()
        session.status = status
        # Stub the chat-history lookup that _get_original_message performs.
        query = session.chat.messages.filter.return_value.order_by.return_value
        query.first.return_value = first_human_message
        return session

    def _make_message(self, content, message_id=1):
        message = MagicMock()
        message.content = content
        message.id = message_id
        return message

    def _make_experiment(self, consent_enabled=True, consent_form_id=1, seed_message=None):
        experiment = MagicMock()
        experiment.conversational_consent_enabled = consent_enabled
        experiment.consent_form_id = consent_form_id
        experiment.seed_message = seed_message
        experiment.consent_form.consent_text = "Do you consent?"
        experiment.consent_form.confirmation_text = "Type 1 to agree"
        return experiment

    def test_should_not_run_without_consent_support(self):
        capabilities = make_capabilities(supports_conversational_consent=False)
        session = self._make_session()
        experiment = self._make_experiment()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            capabilities=capabilities,
        )
        assert self.stage.should_run(ctx) is False

    def test_should_not_run_when_consent_not_enabled(self):
        session = self._make_session()
        experiment = self._make_experiment(consent_enabled=False)
        ctx = make_context(experiment=experiment, experiment_session=session)
        assert self.stage.should_run(ctx) is False

    def test_should_not_run_when_session_active(self):
        session = self._make_session(status=SessionStatus.ACTIVE)
        experiment = self._make_experiment()
        ctx = make_context(experiment=experiment, experiment_session=session)
        assert self.stage.should_run(ctx) is False

    def test_setup_transitions_to_pending(self):
        session = self._make_session(status=SessionStatus.SETUP)
        experiment = self._make_experiment()
        ctx = make_context(experiment=experiment, experiment_session=session)

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        session.update_status.assert_called_once_with(SessionStatus.PENDING)
        assert "Do you consent?" in exc_info.value.response
        assert "Type 1 to agree" in exc_info.value.response

    def test_pending_non_consent_input_repeats_prompt(self):
        session = self._make_session(status=SessionStatus.PENDING)
        experiment = self._make_experiment()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="no thanks",
        )

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        assert "Do you consent?" in exc_info.value.response

    @pytest.mark.parametrize(
        (
            "first_human_message_content",
            "seed_message",
            "has_bot",
            "expected_exception",
            "expected_input",
            "expected_human_message",
            "expected_response",
        ),
        [
            pytest.param(
                "1",
                None,
                False,
                EarlyAbort,
                None,
                None,
                None,
                id="no-original-or-seed-halts-silently",
            ),
            pytest.param(
                "How do I reset my password?",
                "Welcome!",
                True,
                EarlyExitResponse,
                "How do I reset my password?",
                "original",
                "Here's how to reset your password",
                id="original-message-wins-over-seed",
            ),
            pytest.param(
                "1",
                "Welcome!",
                True,
                EarlyExitResponse,
                "Welcome!",
                None,
                "Hi there",
                id="falls-back-to-seed-when-first-message-is-consent-token",
            ),
        ],
    )
    def test_pending_consent_activation(
        self,
        first_human_message_content,
        seed_message,
        has_bot,
        expected_exception,
        expected_input,
        expected_human_message,
        expected_response,
    ):
        original = self._make_message(first_human_message_content)
        session = self._make_session(status=SessionStatus.PENDING, first_human_message=original)
        experiment = self._make_experiment(seed_message=seed_message)
        bot = None
        if has_bot:
            bot = MagicMock()
            bot.process_input.return_value.content = expected_response
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
            bot=bot,
        )

        with pytest.raises(expected_exception) as exc_info:
            self.stage(ctx)

        # The session is always activated once consent is accepted.
        session.update_status.assert_called_with(SessionStatus.ACTIVE)

        if expected_exception is EarlyAbort:
            # Nothing to send: halt silently so the consent token is consumed
            # without forwarding it to the bot or persisting an empty message.
            return

        # The original message (when present) is passed through as the existing
        # human_message so no duplicate HUMAN record is created; otherwise the
        # seed message is sent with no linked human_message.
        expected_hm = original if expected_human_message == "original" else None
        bot.process_input.assert_called_once_with(user_input=expected_input, human_message=expected_hm)
        assert exc_info.value.response == expected_response
        assert ctx.bot_response is bot.process_input.return_value

    def test_pending_consent_discards_consent_token_message(self):
        # The consent token persisted by ChatMessageCreationStage must be removed
        # so it doesn't pollute the bot history with a stray HUMAN message.
        consent_token_message = self._make_message("1", message_id=42)
        original = self._make_message("How do I reset my password?", message_id=7)
        session = self._make_session(status=SessionStatus.PENDING, first_human_message=original)
        experiment = self._make_experiment(seed_message="Welcome!")
        bot = MagicMock()
        bot.process_input.return_value.content = "answer"
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
            bot=bot,
            human_message=consent_token_message,
        )

        with pytest.raises(EarlyExitResponse):
            self.stage(ctx)

        consent_token_message.delete.assert_called_once_with()
        assert ctx.human_message is None
        # The trace is re-pointed to the message actually being answered.
        ctx.trace_service.set_input_message_id.assert_called_once_with(original.id)
