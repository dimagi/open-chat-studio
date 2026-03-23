from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.stages.core import ConsentFlowStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.experiments.models import SessionStatus


class TestConsentFlowStage:
    def setup_method(self):
        self.stage = ConsentFlowStage()

    def _make_session(self, status=SessionStatus.SETUP):
        session = MagicMock()
        session.status = status
        return session

    def _make_experiment(self, consent_enabled=True, consent_form_id=1, pre_survey=None, seed_message=None):
        experiment = MagicMock()
        experiment.conversational_consent_enabled = consent_enabled
        experiment.consent_form_id = consent_form_id
        experiment.pre_survey = pre_survey
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

    def test_pending_consent_no_survey_activates(self):
        session = self._make_session(status=SessionStatus.PENDING)
        experiment = self._make_experiment(pre_survey=None, seed_message=None)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
        )

        # No early exit when no seed message
        self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.ACTIVE)

    def test_pending_consent_with_survey(self):
        pre_survey = MagicMock()
        pre_survey.confirmation_text = "Complete survey: {survey_link}"
        session = self._make_session(status=SessionStatus.PENDING)
        session.get_pre_survey_link.return_value = "https://survey.example.com"
        experiment = self._make_experiment(pre_survey=pre_survey)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
        )

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.PENDING_PRE_SURVEY)
        assert "https://survey.example.com" in exc_info.value.response

    def test_pre_survey_consent_activates(self):
        session = self._make_session(status=SessionStatus.PENDING_PRE_SURVEY)
        experiment = self._make_experiment(seed_message=None)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            user_query="1",
        )

        # No early exit when no seed message
        self.stage(ctx)

        session.update_status.assert_called_with(SessionStatus.ACTIVE)
