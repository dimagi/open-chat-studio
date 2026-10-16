import pytest
from waffle.testutils import override_flag

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant
from apps.moderation.const import MAX_BLOCK_REASON_LENGTH
from apps.moderation.enforcement import (
    block_participant,
    block_participant_from_code_node,
    is_exempt,
    is_participant_blocked,
    unblock_participant,
)
from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory
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
@pytest.mark.parametrize(
    "blocked",
    [
        pytest.param(True, id="blocked"),
        pytest.param(False, id="not_blocked"),
    ],
)
def test_unblock_participant(blocked):
    participant = ParticipantFactory.create(strikes=2)
    if blocked:
        DeniedParticipantFactory.create(team=participant.team, participant=participant)

    assert unblock_participant(participant) is blocked

    assert not DeniedParticipant.objects.filter(participant=participant).exists()
    assert Participant.objects.get(id=participant.id).strikes == 0


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

    def test_uses_one_query(self, django_assert_num_queries):
        membership = MembershipFactory.create()
        participant = ParticipantFactory.create(team=membership.team, user=membership.user)
        participant = Participant.objects.get(id=participant.id)

        with django_assert_num_queries(1):
            assert is_exempt(participant=participant, team=membership.team) is True

    def test_user_who_is_not_a_member_is_not_exempt(self):
        participant = ParticipantFactory.create(user=UserFactory.create())

        assert is_exempt(participant=participant, team=participant.team) is False

    def test_participant_without_user_is_not_exempt(self):
        participant = ParticipantFactory.create()

        assert is_exempt(participant=participant, team=participant.team) is False


@pytest.mark.django_db()
class TestBlockParticipantFromCodeNode:
    @pytest.fixture()
    def session(self, team_flag):
        session = ExperimentSessionFactory.create()
        team_flag(FLAG, session.team)
        return session

    def test_blocks_the_participant_without_strikes(self, session):
        created = block_participant_from_code_node(session=session, reason="Repeated threats")

        assert created is True
        denial = DeniedParticipant.objects.get()
        assert denial.team_id == session.team_id
        assert denial.participant_id == session.participant_id
        assert denial.source == DeniedParticipantSource.CODE_NODE
        assert denial.reason == "Repeated threats"
        assert denial.experiment_id == session.experiment_id
        assert denial.chat_message_id is None
        assert denial.created_by_id is None
        assert Participant.objects.get(id=session.participant_id).strikes == 0

    def test_truncates_a_long_reason(self, session):
        block_participant_from_code_node(session=session, reason="x" * (MAX_BLOCK_REASON_LENGTH + 1))

        assert DeniedParticipant.objects.get().reason == "x" * MAX_BLOCK_REASON_LENGTH

    def test_second_call_keeps_the_first_block(self, session):
        block_participant_from_code_node(session=session, reason="First")

        created = block_participant_from_code_node(session=session, reason="Second")

        assert created is False
        assert DeniedParticipant.objects.get().reason == "First"

    @override_flag(FLAG, active=False)
    def test_does_nothing_when_the_flag_is_off(self):
        session = ExperimentSessionFactory.create()

        assert block_participant_from_code_node(session=session, reason="Spam") is False
        assert not DeniedParticipant.objects.exists()

    @pytest.mark.parametrize(
        ("is_member", "blocked"),
        [
            pytest.param(True, False, id="team_member_is_exempt"),
            pytest.param(False, True, id="user_from_another_team_is_not_exempt"),
        ],
    )
    def test_exempt_participant_is_not_blocked(self, session, is_member, blocked):
        membership = MembershipFactory.create(team=session.team if is_member else TeamFactory.create())
        session.participant.user = membership.user
        session.participant.save()

        assert block_participant_from_code_node(session=session, reason="Spam") is blocked
        assert DeniedParticipant.objects.exists() is blocked

    def test_does_nothing_in_an_evaluation_run(self, session):
        session.platform = ChannelPlatform.EVALUATIONS
        session.save()

        assert block_participant_from_code_node(session=session, reason="Spam") is False
        assert not DeniedParticipant.objects.exists()
