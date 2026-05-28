import pytest

from apps.channels.channels_v2.stages.core import ParticipantResolverStage
from apps.channels.models import ChannelPlatform
from apps.channels.tests.channels.conftest import make_context
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


@pytest.mark.django_db()
class TestParticipantResolverStage:
    def setup_method(self):
        self.stage = ParticipantResolverStage()

    def test_sets_participant_when_exists(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team,
            identifier="known_user",
            platform=experiment_channel.platform,
        )

        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            participant_identifier="known_user",
        )
        self.stage(ctx)

        assert ctx.participant is not None
        assert ctx.participant.id == participant.id

    def test_creates_participant_for_new_user(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)

        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            participant_identifier="brand_new_user",
        )
        self.stage(ctx)

        assert ctx.participant is not None
        assert ctx.participant.identifier == "brand_new_user"
        assert ctx.participant.platform == experiment_channel.platform
        assert ctx.participant.team == experiment.team

    def test_does_not_match_participant_on_different_platform(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        # Pre-create a participant on a different platform with the same identifier
        other_platform = next(p for p in ChannelPlatform if p != experiment_channel.platform_enum)
        other_participant = ParticipantFactory(
            team=experiment.team,
            identifier="cross_platform_user",
            platform=other_platform,
        )

        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            participant_identifier="cross_platform_user",
        )
        self.stage(ctx)

        # A new participant is created on the channel's platform, not the other one
        assert ctx.participant is not None
        assert ctx.participant.id != other_participant.id
        assert ctx.participant.platform == experiment_channel.platform
