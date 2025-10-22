"""
Tests for API access control with deny list support.
"""
import pytest

from apps.experiments.const import ParticipantAccessLevel
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory


@pytest.mark.django_db()
class TestAPIAccessControl:
    """Test API access control with different access levels"""

    def test_open_access_allows_api_access(self):
        """Open access experiments should be accessible via API"""
        experiment = ExperimentFactory(
            participant_access_level=ParticipantAccessLevel.OPEN,
            participant_allowlist=[],
            participant_denylist=[]
        )
        
        assert experiment.is_public is True
        assert experiment.is_participant_allowed("anyone@example.com") is True

    def test_allow_list_restricts_api_access(self):
        """Allow list experiments should restrict API access"""
        experiment = ExperimentFactory(
            participant_access_level=ParticipantAccessLevel.ALLOW_LIST,
            participant_allowlist=["allowed@example.com"],
            participant_denylist=[]
        )
        
        assert experiment.is_public is False
        assert experiment.is_participant_allowed("allowed@example.com") is True
        assert experiment.is_participant_allowed("other@example.com") is False

    def test_deny_list_blocks_api_access(self):
        """Deny list experiments should block specific participants via API"""
        experiment = ExperimentFactory(
            participant_access_level=ParticipantAccessLevel.DENY_LIST,
            participant_allowlist=[],
            participant_denylist=["blocked@example.com"]
        )
        
        assert experiment.is_public is False
        assert experiment.is_participant_allowed("blocked@example.com") is False
        assert experiment.is_participant_allowed("allowed@example.com") is True

    def test_team_members_bypass_restrictions(self):
        """Team members should always have access regardless of restrictions"""
        experiment = ExperimentFactory(
            participant_access_level=ParticipantAccessLevel.DENY_LIST,
            participant_denylist=["member@example.com"]
        )
        
        MembershipFactory(team=experiment.team, user__email="member@example.com")
        
        # Team member should be allowed even though they're on the deny list
        assert experiment.is_participant_allowed("member@example.com") is True
