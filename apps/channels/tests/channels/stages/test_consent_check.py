from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.capabilities import PlatformConsentConfig
from apps.channels.channels_v2.exceptions import EarlyAbort
from apps.channels.channels_v2.stages.core import ConsentCheckStage
from apps.channels.models import ChannelPlatform
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ParticipantFactory


class TestConsentCheckStageShouldRun:
    """should_run() does not hit the DB -- pure precondition checks."""

    def setup_method(self):
        self.stage = ConsentCheckStage()

    def test_skips_when_no_session(self):
        ctx = make_context(
            experiment_session=None,
            capabilities=make_capabilities(consent_config=PlatformConsentConfig()),
        )
        assert self.stage.should_run(ctx) is False

    def test_skips_when_no_consent_config_configured(self):
        ctx = make_context(
            experiment_session=MagicMock(),
            capabilities=make_capabilities(consent_config=None),
        )
        assert self.stage.should_run(ctx) is False

    def test_runs_when_session_and_config_present(self):
        ctx = make_context(
            experiment_session=MagicMock(),
            capabilities=make_capabilities(consent_config=PlatformConsentConfig()),
        )
        assert self.stage.should_run(ctx) is True


@pytest.mark.django_db()
class TestConsentCheckStageProcess:
    """process() reads ParticipantData via ctx.participant_data."""

    def setup_method(self):
        self.stage = ConsentCheckStage()

    def _make_participant_data(self, experiment, *, consent=None):
        participant = ParticipantFactory.create(team=experiment.team, platform=ChannelPlatform.COMMCARE_CONNECT)
        metadata = {} if consent is None else {"consent": consent}
        ParticipantData.objects.create(
            team=experiment.team,
            participant=participant,
            experiment=experiment,
            system_metadata=metadata,
        )
        return participant

    def test_passes_when_consent_true(self, experiment):
        participant = self._make_participant_data(experiment, consent=True)
        ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier=participant.identifier,
            capabilities=make_capabilities(consent_config=PlatformConsentConfig(strict=True, default_consent=False)),
        )

        self.stage.process(ctx)  # does not raise

    def test_aborts_when_consent_false(self, experiment):
        """Both strict and lenient configs must block an explicit consent=False."""
        participant = self._make_participant_data(experiment, consent=False)
        ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier=participant.identifier,
            capabilities=make_capabilities(
                consent_config=PlatformConsentConfig(strict=False, default_consent=True),
            ),
        )

        with pytest.raises(EarlyAbort):
            self.stage.process(ctx)

    def test_strict_aborts_when_no_participant_data(self, experiment):
        ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier="ghost",
            capabilities=make_capabilities(consent_config=PlatformConsentConfig(strict=True, default_consent=False)),
        )

        with pytest.raises(EarlyAbort):
            self.stage.process(ctx)

    def test_lenient_passes_when_no_participant_data(self, experiment):
        ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier="ghost",
            capabilities=make_capabilities(consent_config=PlatformConsentConfig(strict=False, default_consent=True)),
        )

        self.stage.process(ctx)  # does not raise

    def test_default_consent_governs_missing_key(self, experiment):
        """When the ParticipantData row exists but has no 'consent' key,
        default_consent decides the outcome."""
        participant = self._make_participant_data(experiment, consent=None)

        lenient_ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier=participant.identifier,
            capabilities=make_capabilities(consent_config=PlatformConsentConfig(default_consent=True)),
        )
        self.stage.process(lenient_ctx)  # does not raise

        strict_ctx = make_context(
            experiment=experiment,
            experiment_session=MagicMock(),
            participant_identifier=participant.identifier,
            capabilities=make_capabilities(consent_config=PlatformConsentConfig(default_consent=False)),
        )
        with pytest.raises(EarlyAbort):
            self.stage.process(strict_ctx)
