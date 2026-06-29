import pytest

from apps.channels.models import ChannelPlatform
from apps.chat.channels import _start_experiment_session
from apps.experiments.models import Participant
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


@pytest.fixture()
def experiment(db):
    return ExperimentFactory()


@pytest.fixture()
def whatsapp_channel(experiment):
    return ExperimentChannelFactory(
        experiment=experiment,
        team=experiment.team,
        platform=ChannelPlatform.WHATSAPP,
    )


@pytest.mark.django_db()
class TestStartExperimentSessionParticipant:
    """Covers how the session is linked to its participant."""

    def test_creates_participant_with_normalized_identifier(self, experiment, whatsapp_channel):
        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant=Participant(identifier="bsuid_user_1"),
        )

        assert session.participant.identifier == "bsuid_user_1"
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_user_1").count() == 1

    def test_reuses_existing_participant_for_identifier(self, experiment, whatsapp_channel):
        existing = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="bsuid_user_2",
        )

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant=Participant(identifier="bsuid_user_2"),
        )

        assert session.participant.id == existing.id
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_user_2").count() == 1

    def test_uses_provided_participant(self, experiment, whatsapp_channel):
        """When the caller passes a stored participant (e.g. the v2 pipeline) it is used
        directly -- no second lookup, no new participant created."""
        resolved = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="+15551234567",
        )

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant=resolved,
        )

        assert session.participant.id == resolved.id
        assert Participant.objects.filter(team=experiment.team, platform=ChannelPlatform.WHATSAPP).count() == 1
