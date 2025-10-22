"""
Tests for channel behavior with deny list support.
"""
import pytest

from apps.experiments.const import ParticipantAccessLevel
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory
from apps.channels.tests.test_base_channel_behavior import TestChannel, base_messages


@pytest.fixture()
def test_channel(db):
    experiment = ExperimentFactory(conversational_consent_enabled=False)
    channel = ExperimentChannelFactory(experiment=experiment)
    channel = TestChannel(experiment=experiment, experiment_channel=channel)
    return channel


@pytest.mark.django_db()
class TestChannelWithDenyList:
    """Test channel behavior with deny list access control"""

    def test_deny_list_blocks_listed_participants(self, test_channel):
        """Participants on deny list should be blocked"""
        message = base_messages.text_message(participant_id="blocked@example.com")
        experiment = test_channel.experiment
        
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = ["blocked@example.com"]
        experiment.participant_allowlist = []
        experiment.save()
        
        test_channel.message = message
        assert test_channel._participant_is_allowed() is False
        
        resp = test_channel.new_user_message(message)
        assert resp.content == "Sorry, you are not allowed to chat to this bot"
        assert test_channel.text_sent[0] == "Sorry, you are not allowed to chat to this bot"

    def test_deny_list_allows_unlisted_participants(self, test_channel):
        """Participants not on deny list should be allowed"""
        message = base_messages.text_message(participant_id="allowed@example.com")
        experiment = test_channel.experiment
        
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = ["blocked@example.com"]
        experiment.participant_allowlist = []
        experiment.save()
        
        test_channel.message = message
        assert test_channel._participant_is_allowed() is True

    def test_deny_list_allows_team_members_even_if_denied(self, test_channel):
        """Team members should always be allowed even if on deny list"""
        team_member_email = "member@example.com"
        message = base_messages.text_message(participant_id=team_member_email)
        experiment = test_channel.experiment
        
        MembershipFactory(team=experiment.team, user__email=team_member_email)
        
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_denylist = [team_member_email]
        experiment.participant_allowlist = []
        experiment.save()
        
        test_channel.message = message
        assert test_channel._participant_is_allowed() is True

    def test_open_access_allows_everyone(self, test_channel):
        """Open access should allow all participants"""
        message = base_messages.text_message(participant_id="anyone@example.com")
        experiment = test_channel.experiment
        
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.participant_denylist = []
        experiment.save()
        
        test_channel.message = message
        assert test_channel._participant_is_allowed() is True


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("access_level", "allowlist", "denylist", "is_external_user", "identifier", "is_allowed"),
    [
        # OPEN access tests
        (ParticipantAccessLevel.OPEN, [], [], True, "11111", True),
        (ParticipantAccessLevel.OPEN, [], [], True, "anyone@example.com", True),
        
        # ALLOW_LIST tests
        (ParticipantAccessLevel.ALLOW_LIST, ["11111"], [], True, "11111", True),
        (ParticipantAccessLevel.ALLOW_LIST, ["11111"], [], True, "22222", False),
        (ParticipantAccessLevel.ALLOW_LIST, ["alice@example.com"], [], True, "alice@example.com", True),
        (ParticipantAccessLevel.ALLOW_LIST, ["alice@example.com"], [], True, "bob@example.com", False),
        (ParticipantAccessLevel.ALLOW_LIST, [], [], False, "member@test.com", True),  # Team member
        
        # DENY_LIST tests
        (ParticipantAccessLevel.DENY_LIST, [], ["11111"], True, "11111", False),
        (ParticipantAccessLevel.DENY_LIST, [], ["11111"], True, "22222", True),
        (ParticipantAccessLevel.DENY_LIST, [], ["blocked@example.com"], True, "blocked@example.com", False),
        (ParticipantAccessLevel.DENY_LIST, [], ["blocked@example.com"], True, "allowed@example.com", True),
        (ParticipantAccessLevel.DENY_LIST, [], ["member@test.com"], False, "member@test.com", True),  # Team member
    ],
)
def test_participant_authorization_with_access_levels(
    access_level, allowlist, denylist, is_external_user, identifier, is_allowed, test_channel
):
    """Comprehensive test for participant authorization with different access levels"""
    message = base_messages.text_message(participant_id=identifier)
    experiment = test_channel.experiment
    
    if not is_external_user:
        MembershipFactory(team=experiment.team, user__email=identifier)
    
    experiment.participant_access_level = access_level
    experiment.participant_allowlist = allowlist
    experiment.participant_denylist = denylist
    experiment.save()
    
    test_channel.message = message
    assert test_channel._participant_is_allowed() == is_allowed
    
    if not is_allowed:
        resp = test_channel.new_user_message(message)
        assert resp.content == "Sorry, you are not allowed to chat to this bot"
        assert test_channel.text_sent[0] == "Sorry, you are not allowed to chat to this bot"
