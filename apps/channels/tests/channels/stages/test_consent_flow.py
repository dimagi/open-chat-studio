from unittest.mock import MagicMock

import pytest

from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.stages.core import ConsentFlowStage
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

    @pytest.mark.parametrize(
        ("first_human_message_content", "seed_message", "expected_query"),
        [
            pytest.param(
                "How do I reset my password?",
                "Welcome!",
                "How do I reset my password?",
                id="original-message-wins-over-seed",
            ),
            pytest.param(
                "1",
                "Welcome!",
                "Welcome!",
                id="falls-back-to-seed-when-first-message-is-consent-token",
            ),
            pytest.param(
                "1",
                None,
                None,
                id="no-original-or-seed-halts-silently",
            ),
        ],
    )
    def test_pending_consent_activation(self, first_human_message_content, seed_message, expected_query):
        consent_token_message = self._make_message("1")
        session = self._make_session(
            status=SessionStatus.PENDING,
            first_human_message=self._make_message(first_human_message_content),
        )
        experiment = self._make_experiment(seed_message=seed_message)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
            human_message=consent_token_message,
        )

        if expected_query is None:
            # Nothing to answer: halt silently (EarlyAbort) so the consent token
            # is consumed without forwarding it to the bot or sending/persisting
            # an empty message.
            with pytest.raises(EarlyAbort):
                self.stage(ctx)
        else:
            # The stage returns normally so the pipeline continues into
            # BotInteractionStage with the swapped-in query. The consent-token
            # message stays as ctx.human_message: the bot excludes the input
            # message from the LLM history, keeping the token out of the
            # LLM context while preserving it in the persisted history.
            self.stage(ctx)

            assert ctx.user_query == expected_query
            assert ctx.human_message is consent_token_message
            assert ctx.bot_response is None

        session.update_status.assert_called_with(SessionStatus.ACTIVE)
