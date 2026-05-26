import pytest
from django.db.models import Q

from apps.channels.models import ChannelPlatform
from apps.chat.channels import _start_experiment_session
from apps.experiments.models import Participant
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.user import UserFactory


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
class TestStartExperimentSessionParticipantFilter:
    """Covers the participant_id_filter branches added for BSUID/legacy phone continuity."""

    def test_default_filter_creates_participant_with_normalized_identifier(self, experiment, whatsapp_channel):
        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="bsuid_user_1",
        )

        assert session.participant.identifier == "bsuid_user_1"
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_user_1").count() == 1

    def test_simple_filter_reuses_existing_participant(self, experiment, whatsapp_channel):
        existing = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="bsuid_user_2",
        )

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="bsuid_user_2",
            participant_id_filter=Q(identifier="bsuid_user_2"),
        )

        assert session.participant.id == existing.id
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_user_2").count() == 1

    def test_disjunction_filter_reuses_legacy_phone_participant(self, experiment, whatsapp_channel):
        """When the inbound BSUID is new but a legacy phone-keyed participant exists,
        the session is attached to the legacy participant to preserve continuity."""
        legacy = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="+15551234567",
        )

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="bsuid_new",
            participant_id_filter=Q(identifier="bsuid_new") | Q(identifier="+15551234567"),
        )

        assert session.participant.id == legacy.id
        # No new participant is created for the BSUID -- the legacy phone row is reused.
        assert not Participant.objects.filter(
            team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="bsuid_new"
        ).exists()

    def test_disjunction_filter_with_no_match_creates_with_normalized_identifier(self, experiment, whatsapp_channel):
        """When neither the BSUID nor the legacy phone matches anything, fall back to
        get_or_create using the (canonical) normalized identifier -- not the phone."""
        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="bsuid_canonical",
            participant_id_filter=Q(identifier="bsuid_canonical") | Q(identifier="+15559999999"),
        )

        assert session.participant.identifier == "bsuid_canonical"
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_canonical").exists()
        assert not Participant.objects.filter(team=experiment.team, identifier="+15559999999").exists()

    def test_disjunction_filter_with_multiple_matches_picks_oldest(self, experiment, whatsapp_channel):
        """If both the BSUID and the legacy phone participant rows exist, the oldest by
        created_at wins so we don't fork the conversation onto the newer row."""
        oldest = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="+15551112222",
        )
        ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="bsuid_oldest_wins",
        )

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="bsuid_oldest_wins",
            participant_id_filter=Q(identifier="bsuid_oldest_wins") | Q(identifier="+15551112222"),
        )

        assert session.participant.id == oldest.id

    def test_disjunction_filter_backfills_user_on_existing_participant(self, experiment, whatsapp_channel):
        legacy = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="user@example.com",
            user=None,
        )
        user = UserFactory(username="user@example.com")

        session = _start_experiment_session(
            working_experiment=experiment,
            experiment_channel=whatsapp_channel,
            participant_identifier="user@example.com",
            participant_user=user,
            participant_id_filter=Q(identifier="user@example.com") | Q(identifier="+15553334444"),
        )

        legacy.refresh_from_db()
        assert session.participant.id == legacy.id
        assert legacy.user_id == user.id
