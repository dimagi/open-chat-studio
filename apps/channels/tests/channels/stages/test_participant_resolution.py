import pytest
from django.db.models import Q

from apps.channels.channels_v2.stages.core import ParticipantResolverStage, get_or_create_participant
from apps.channels.datamodels import WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples import meta_cloud_api_messages
from apps.experiments.models import Participant
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.user import UserFactory

BSUID = "US.13491208655302741918"
PHONE = "27456897512"


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

    def _whatsapp_context(self, experiment, experiment_channel):
        message = WhatsAppMessage.parse(meta_cloud_api_messages.text_message_value())
        return make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            participant_identifier=BSUID,
            message=message,
        )

    def test_matches_legacy_phone_participant_when_bsuid_is_new(self):
        """A returning user previously keyed by phone is reused when their BSUID first arrives."""
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(
            experiment=experiment, team=experiment.team, platform=ChannelPlatform.WHATSAPP
        )
        legacy = ParticipantFactory(team=experiment.team, identifier=PHONE, platform=ChannelPlatform.WHATSAPP)

        ctx = self._whatsapp_context(experiment, experiment_channel)
        self.stage(ctx)

        assert ctx.participant.id == legacy.id
        legacy.refresh_from_db()
        assert legacy.remote_id == PHONE

    def test_persists_phone_number_on_remote_id(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(
            experiment=experiment, team=experiment.team, platform=ChannelPlatform.WHATSAPP
        )

        ctx = self._whatsapp_context(experiment, experiment_channel)
        self.stage(ctx)

        assert ctx.participant.identifier == BSUID
        ctx.participant.refresh_from_db()
        assert ctx.participant.remote_id == PHONE


@pytest.mark.django_db()
class TestGetOrCreateParticipant:
    """Covers the BSUID/legacy-phone continuity branches of get_or_create_participant."""

    def test_simple_filter_reuses_existing_participant(self):
        experiment = ExperimentFactory()
        existing = ParticipantFactory(team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="bsuid_user")

        participant = get_or_create_participant(
            team=experiment.team,
            normalized_identifier="bsuid_user",
            platform=ChannelPlatform.WHATSAPP,
            participant_user=None,
            participant_id_filter=Q(identifier="bsuid_user"),
        )

        assert participant.id == existing.id
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_user").count() == 1

    def test_disjunction_reuses_legacy_phone_participant_when_bsuid_is_new(self):
        """When the inbound BSUID is new but a legacy phone-keyed participant exists,
        the legacy participant is reused to preserve continuity."""
        experiment = ExperimentFactory()
        legacy = ParticipantFactory(team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="+15551234567")

        participant = get_or_create_participant(
            team=experiment.team,
            normalized_identifier="bsuid_new",
            platform=ChannelPlatform.WHATSAPP,
            participant_user=None,
            participant_id_filter=Q(identifier="bsuid_new") | Q(identifier="+15551234567"),
        )

        assert participant.id == legacy.id
        # No new participant is created for the BSUID -- the legacy phone row is reused.
        assert not Participant.objects.filter(
            team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="bsuid_new"
        ).exists()

    def test_disjunction_with_no_match_creates_with_normalized_identifier(self):
        """When neither the BSUID nor the legacy phone matches, fall back to get_or_create
        using the canonical normalized identifier -- not the phone."""
        experiment = ExperimentFactory()

        participant = get_or_create_participant(
            team=experiment.team,
            normalized_identifier="bsuid_canonical",
            platform=ChannelPlatform.WHATSAPP,
            participant_user=None,
            participant_id_filter=Q(identifier="bsuid_canonical") | Q(identifier="+15559999999"),
        )

        assert participant.identifier == "bsuid_canonical"
        assert Participant.objects.filter(team=experiment.team, identifier="bsuid_canonical").exists()
        assert not Participant.objects.filter(team=experiment.team, identifier="+15559999999").exists()

    def test_disjunction_with_multiple_matches_picks_oldest(self):
        """If both the BSUID and the legacy phone rows exist, the oldest by created_at wins
        so the conversation isn't forked onto the newer row."""
        experiment = ExperimentFactory()
        oldest = ParticipantFactory(team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="+15551112222")
        ParticipantFactory(team=experiment.team, platform=ChannelPlatform.WHATSAPP, identifier="bsuid_oldest_wins")

        participant = get_or_create_participant(
            team=experiment.team,
            normalized_identifier="bsuid_oldest_wins",
            platform=ChannelPlatform.WHATSAPP,
            participant_user=None,
            participant_id_filter=Q(identifier="bsuid_oldest_wins") | Q(identifier="+15551112222"),
        )

        assert participant.id == oldest.id

    def test_disjunction_backfills_user_on_existing_participant(self):
        experiment = ExperimentFactory()
        legacy = ParticipantFactory(
            team=experiment.team,
            platform=ChannelPlatform.WHATSAPP,
            identifier="user@example.com",
            user=None,
        )
        user = UserFactory(username="user@example.com")

        participant = get_or_create_participant(
            team=experiment.team,
            normalized_identifier="user@example.com",
            platform=ChannelPlatform.WHATSAPP,
            participant_user=user,
            participant_id_filter=Q(identifier="user@example.com") | Q(identifier="+15553334444"),
        )

        legacy.refresh_from_db()
        assert participant.id == legacy.id
        assert legacy.user_id == user.id
