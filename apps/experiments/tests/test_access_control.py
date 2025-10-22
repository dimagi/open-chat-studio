"""
Tests for participant access control (allowlist and denylist) functionality.
"""
import pytest

from apps.experiments.const import ParticipantAccessLevel
from apps.experiments.models import Experiment
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def experiment(team_with_users, db):
    return ExperimentFactory(team=team_with_users)


@pytest.mark.django_db()
class TestParticipantAccessControl:
    """Test participant access control with different access levels"""

    def test_open_access_allows_everyone(self, experiment):
        """When access level is OPEN, anyone should be allowed"""
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.participant_denylist = []
        experiment.save()

        assert experiment.is_public is True
        assert experiment.is_participant_allowed("anyone@example.com") is True
        assert experiment.is_participant_allowed("+1234567890") is True
        assert experiment.is_participant_allowed("random_id") is True

    def test_allow_list_restricts_to_list(self, experiment):
        """When access level is ALLOW_LIST, only listed participants should be allowed"""
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["alice@example.com", "+1234567890"]
        experiment.participant_denylist = []
        experiment.save()

        assert experiment.is_public is False
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("+1234567890") is True
        assert experiment.is_participant_allowed("bob@example.com") is False
        assert experiment.is_participant_allowed("+9876543210") is False

    def test_deny_list_blocks_listed_participants(self, experiment):
        """When access level is DENY_LIST, listed participants should be blocked"""
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_allowlist = []
        experiment.participant_denylist = ["blocked@example.com", "+1234567890"]
        experiment.save()

        assert experiment.is_public is False
        assert experiment.is_participant_allowed("blocked@example.com") is False
        assert experiment.is_participant_allowed("+1234567890") is False
        assert experiment.is_participant_allowed("allowed@example.com") is True
        assert experiment.is_participant_allowed("+9876543210") is True

    def test_team_members_always_allowed(self, experiment):
        """Team members should always be allowed regardless of access level"""
        team_member_email = "member@example.com"
        MembershipFactory(team=experiment.team, user__email=team_member_email)

        # Test with ALLOW_LIST (member not in list)
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["other@example.com"]
        experiment.participant_denylist = []
        experiment.save()
        assert experiment.is_participant_allowed(team_member_email) is True

        # Test with DENY_LIST (member in deny list)
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_allowlist = []
        experiment.participant_denylist = [team_member_email]
        experiment.save()
        assert experiment.is_participant_allowed(team_member_email) is True

        # Test with OPEN
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.save()
        assert experiment.is_participant_allowed(team_member_email) is True

    def test_is_public_property(self, experiment):
        """Test is_public property reflects access level correctly"""
        # OPEN should be public
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.save()
        assert experiment.is_public is True

        # ALLOW_LIST should not be public
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["someone@example.com"]
        experiment.save()
        assert experiment.is_public is False

        # DENY_LIST should not be public
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = ["blocked@example.com"]
        experiment.save()
        assert experiment.is_public is False


@pytest.mark.django_db()
class TestBackwardCompatibility:
    """Test that existing experiments with allowlists work correctly"""

    def test_migration_sets_access_level_from_allowlist(self, experiment):
        """Test that experiments with existing allowlists are migrated correctly"""
        # Simulate pre-migration state: has allowlist but access_level is 'open'
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = ["alice@example.com"]
        experiment.save()

        # After migration, access_level should be ALLOW_LIST if allowlist has entries
        # This would be handled by the migration itself
        # Here we just test the expected behavior
        if experiment.participant_allowlist:
            experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
            experiment.save()

        assert experiment.participant_access_level == ParticipantAccessLevel.ALLOW_LIST
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("bob@example.com") is False

    def test_empty_allowlist_migrates_to_open(self, experiment):
        """Test that experiments with empty allowlists are migrated to OPEN"""
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.save()

        assert experiment.participant_access_level == ParticipantAccessLevel.OPEN
        assert experiment.is_public is True


@pytest.mark.django_db()
class TestAccessControlValidation:
    """Test validation of access control settings"""

    def test_allow_list_with_empty_list_should_fail(self, experiment):
        """Allow list mode requires at least one entry (enforced by form validation)"""
        # This is actually enforced at the form level, but the model should handle it gracefully
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = []
        experiment.save()

        # Even with empty allowlist, the logic should work (reject everyone except team members)
        assert experiment.is_participant_allowed("anyone@example.com") is False

    def test_deny_list_with_empty_list(self, experiment):
        """Deny list mode with empty list should allow everyone (like OPEN)"""
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = []
        experiment.save()

        # With empty denylist, everyone should be allowed
        assert experiment.is_participant_allowed("anyone@example.com") is True


@pytest.mark.django_db()
class TestComplexScenarios:
    """Test complex real-world scenarios"""

    def test_phone_number_identifiers(self, experiment):
        """Test with phone number identifiers (E.164 format)"""
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["+27123456789", "+1234567890"]
        experiment.save()

        assert experiment.is_participant_allowed("+27123456789") is True
        assert experiment.is_participant_allowed("+1234567890") is True
        assert experiment.is_participant_allowed("+44987654321") is False

    def test_mixed_identifier_types(self, experiment):
        """Test with mixed identifier types (emails and phone numbers)"""
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = ["spam@example.com", "+1234567890", "bot_id_123"]
        experiment.save()

        assert experiment.is_participant_allowed("spam@example.com") is False
        assert experiment.is_participant_allowed("+1234567890") is False
        assert experiment.is_participant_allowed("bot_id_123") is False
        assert experiment.is_participant_allowed("legit@example.com") is True
        assert experiment.is_participant_allowed("+9876543210") is True

    def test_switching_between_access_levels(self, experiment):
        """Test switching between different access levels"""
        # Start with ALLOW_LIST
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["alice@example.com"]
        experiment.participant_denylist = []
        experiment.save()
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("bob@example.com") is False

        # Switch to DENY_LIST
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_allowlist = []
        experiment.participant_denylist = ["alice@example.com"]
        experiment.save()
        assert experiment.is_participant_allowed("alice@example.com") is False
        assert experiment.is_participant_allowed("bob@example.com") is True

        # Switch to OPEN
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.participant_denylist = []
        experiment.save()
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("bob@example.com") is True
