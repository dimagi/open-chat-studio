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

    def _make_message(self, content):
        message = MagicMock()
        message.content = content
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

    def test_pending_consent_activates_with_no_original_or_seed(self):
        # First message was the consent token itself -> no substantive original
        # message and no seed message.
        session = self._make_session(
            status=SessionStatus.PENDING,
            first_human_message=self._make_message("1"),
        )
        experiment = self._make_experiment(seed_message=None)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
        )

        # Nothing to send: halt silently (EarlyAbort) so the consent token is
        # consumed without sending/persisting an empty message or forwarding it
        # to the bot. The session is still transitioned to ACTIVE.
        with pytest.raises(EarlyAbort):
            self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.ACTIVE)

    def test_pending_consent_answers_original_message(self):
        # The participant's first (pre-consent) message is sent to the bot after
        # consent, so their actual question is answered.
        original = self._make_message("How do I reset my password?")
        session = self._make_session(status=SessionStatus.PENDING, first_human_message=original)
        experiment = self._make_experiment(seed_message="Welcome!")
        bot = MagicMock()
        bot.process_input.return_value.content = "Here's how to reset your password"
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
            bot=bot,
        )

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.ACTIVE)
        # Original message wins over the seed message, and is passed through as
        # the existing human_message so no duplicate HUMAN record is created.
        bot.process_input.assert_called_once_with(user_input="How do I reset my password?", human_message=original)
        assert exc_info.value.response == "Here's how to reset your password"
        assert ctx.bot_response is ctx.bot.process_input.return_value

    def test_pending_consent_falls_back_to_seed_when_first_message_is_consent_token(self):
        # First message was the consent token itself -> fall back to the seed.
        session = self._make_session(
            status=SessionStatus.PENDING,
            first_human_message=self._make_message("1"),
        )
        experiment = self._make_experiment(seed_message="Welcome!")
        bot = MagicMock()
        bot.process_input.return_value.content = "Hi there"
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
            bot=bot,
        )

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.ACTIVE)
        bot.process_input.assert_called_once_with(user_input="Welcome!", human_message=None)
        assert exc_info.value.response == "Hi there"
