import pytest
from waffle.testutils import override_flag

from apps.moderation.enforcement import block_participant, is_exempt, is_participant_blocked
from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory

FLAG = Flags.ABUSE_DETECTION.slug


@pytest.mark.django_db()
class TestIsParticipantBlocked:
    def test_blocked_when_flag_on_for_team(self, team_flag):
        denial = DeniedParticipantFactory.create()
        team_flag(FLAG, denial.team)

        assert is_participant_blocked(team=denial.team, participant_id=denial.participant_id) is True

    @override_flag(FLAG, active=False)
    def test_flag_off_means_not_blocked_even_with_a_row(self):
        denial = DeniedParticipantFactory.create()

        assert is_participant_blocked(team=denial.team, participant_id=denial.participant_id) is False

    @override_flag(FLAG, active=True)
    def test_not_blocked_without_a_row(self):
        participant = ParticipantFactory.create()

        assert is_participant_blocked(team=participant.team, participant_id=participant.id) is False

    @override_flag(FLAG, active=True)
    def test_block_is_scoped_to_its_team(self):
        denial = DeniedParticipantFactory.create()

        assert is_participant_blocked(team=TeamFactory.create(), participant_id=denial.participant_id) is False

    @override_flag(FLAG, active=True)
    def test_missing_participant_is_not_blocked(self):
        assert is_participant_blocked(team=TeamFactory.create(), participant_id=None) is False


@pytest.mark.django_db()
class TestBlockParticipant:
    def test_block_participant_uses_the_participants_team(self):
        participant = ParticipantFactory.create()

        row, created = block_participant(participant=participant, source=DeniedParticipantSource.MANUAL)

        assert created is True
        assert row.team_id == participant.team_id

    def test_duplicate_block_returns_the_existing_row(self):
        denial = DeniedParticipantFactory.create()

        row, created = block_participant(
            participant=denial.participant, source=DeniedParticipantSource.AUTO, reason="second block"
        )

        assert created is False
        assert row == denial
        assert DeniedParticipant.objects.filter(participant_id=denial.participant_id).count() == 1


@pytest.mark.django_db()
class TestIsExempt:
    @pytest.mark.parametrize(
        ("remove_membership", "expected"),
        [
            pytest.param(False, True, id="current_member_is_exempt"),
            pytest.param(True, False, id="former_member_is_not_exempt"),
        ],
    )
    def test_team_member_user(self, remove_membership, expected):
        membership = MembershipFactory.create()
        participant = ParticipantFactory.create(team=membership.team, user=membership.user)
        if remove_membership:
            membership.delete()

        assert is_exempt(participant=participant, team=membership.team) is expected

    def test_user_who_is_not_a_member_is_not_exempt(self):
        participant = ParticipantFactory.create(user=UserFactory.create())

        assert is_exempt(participant=participant, team=participant.team) is False

    def test_participant_without_user_is_not_exempt(self):
        participant = ParticipantFactory.create()

        assert is_exempt(participant=participant, team=participant.team) is False
