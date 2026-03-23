from unittest.mock import MagicMock

from apps.channels.channels_v2.stages.core import SessionActivationStage
from apps.channels.tests.channels.conftest import make_context
from apps.experiments.models import SessionStatus


class TestSessionActivationStage:
    def setup_method(self):
        self.stage = SessionActivationStage()

    def test_should_not_run_when_no_session(self):
        ctx = make_context(experiment_session=None)
        assert self.stage.should_run(ctx) is False

    def test_consent_disabled_activates(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.conversational_consent_enabled = False
        experiment.consent_form_id = 1
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        session.update_status.assert_called_once_with(SessionStatus.ACTIVE)

    def test_no_consent_form_activates(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.conversational_consent_enabled = True
        experiment.consent_form_id = None
        ctx = make_context(experiment=experiment, experiment_session=session)

        self.stage(ctx)

        session.update_status.assert_called_once_with(SessionStatus.ACTIVE)

    def test_consent_enabled_with_form_does_not_run(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.conversational_consent_enabled = True
        experiment.consent_form_id = 1
        ctx = make_context(experiment=experiment, experiment_session=session)

        assert self.stage.should_run(ctx) is False
