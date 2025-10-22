"""
Integration tests for deny list feature across forms and views.
"""
import pytest
from django.urls import reverse

from apps.experiments.const import ParticipantAccessLevel
from apps.experiments.models import Experiment
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def experiment(team_with_users, db):
    return ExperimentFactory(team=team_with_users)


@pytest.mark.django_db()
class TestExperimentFormValidation:
    """Test form validation for access control"""

    def test_open_access_clears_both_lists(self, rf, team_with_users, experiment):
        """When setting to OPEN, both lists should be cleared"""
        from apps.experiments.forms import ExperimentForm
        
        # Create a request with team context
        request = rf.post('/')
        request.team = team_with_users
        request.user = team_with_users.members.first().user
        
        form_data = {
            'name': 'Test Experiment',
            'type': 'llm',
            'participant_access_level': ParticipantAccessLevel.OPEN,
            'participant_allowlist': ['alice@example.com'],  # Should be cleared
            'participant_denylist': ['bob@example.com'],     # Should be cleared
            'llm_provider': experiment.llm_provider.id if experiment.llm_provider else None,
            'llm_provider_model': experiment.llm_provider_model.id if experiment.llm_provider_model else None,
            'prompt_text': 'Test prompt',
        }
        
        form = ExperimentForm(request, data=form_data, instance=experiment)
        if form.is_valid():
            cleaned = form.cleaned_data
            assert cleaned['participant_access_level'] == ParticipantAccessLevel.OPEN
            assert cleaned['participant_allowlist'] == []
            assert cleaned['participant_denylist'] == []

    def test_allow_list_requires_entries(self, rf, team_with_users, experiment):
        """When using ALLOW_LIST, list must not be empty"""
        from apps.experiments.forms import ExperimentForm
        
        request = rf.post('/')
        request.team = team_with_users
        request.user = team_with_users.members.first().user
        
        form_data = {
            'name': 'Test Experiment',
            'type': 'llm',
            'participant_access_level': ParticipantAccessLevel.ALLOW_LIST,
            'participant_allowlist': [],  # Should cause validation error
            'participant_denylist': [],
            'llm_provider': experiment.llm_provider.id if experiment.llm_provider else None,
            'llm_provider_model': experiment.llm_provider_model.id if experiment.llm_provider_model else None,
            'prompt_text': 'Test prompt',
        }
        
        form = ExperimentForm(request, data=form_data, instance=experiment)
        assert not form.is_valid()
        assert 'participant_allowlist' in form.errors or '__all__' in form.errors

    def test_deny_list_requires_entries(self, rf, team_with_users, experiment):
        """When using DENY_LIST, list must not be empty"""
        from apps.experiments.forms import ExperimentForm
        
        request = rf.post('/')
        request.team = team_with_users
        request.user = team_with_users.members.first().user
        
        form_data = {
            'name': 'Test Experiment',
            'type': 'llm',
            'participant_access_level': ParticipantAccessLevel.DENY_LIST,
            'participant_allowlist': [],
            'participant_denylist': [],  # Should cause validation error
            'llm_provider': experiment.llm_provider.id if experiment.llm_provider else None,
            'llm_provider_model': experiment.llm_provider_model.id if experiment.llm_provider_model else None,
            'prompt_text': 'Test prompt',
        }
        
        form = ExperimentForm(request, data=form_data, instance=experiment)
        assert not form.is_valid()
        assert 'participant_denylist' in form.errors or '__all__' in form.errors

    def test_allow_list_clears_denylist(self, rf, team_with_users, experiment):
        """When using ALLOW_LIST, denylist should be cleared"""
        from apps.experiments.forms import ExperimentForm
        
        request = rf.post('/')
        request.team = team_with_users
        request.user = team_with_users.members.first().user
        
        form_data = {
            'name': 'Test Experiment',
            'type': 'llm',
            'participant_access_level': ParticipantAccessLevel.ALLOW_LIST,
            'participant_allowlist': ['alice@example.com'],
            'participant_denylist': ['bob@example.com'],  # Should be cleared
            'llm_provider': experiment.llm_provider.id if experiment.llm_provider else None,
            'llm_provider_model': experiment.llm_provider_model.id if experiment.llm_provider_model else None,
            'prompt_text': 'Test prompt',
        }
        
        form = ExperimentForm(request, data=form_data, instance=experiment)
        if form.is_valid():
            cleaned = form.cleaned_data
            assert cleaned['participant_access_level'] == ParticipantAccessLevel.ALLOW_LIST
            assert 'alice@example.com' in cleaned['participant_allowlist']
            assert cleaned['participant_denylist'] == []

    def test_deny_list_clears_allowlist(self, rf, team_with_users, experiment):
        """When using DENY_LIST, allowlist should be cleared"""
        from apps.experiments.forms import ExperimentForm
        
        request = rf.post('/')
        request.team = team_with_users
        request.user = team_with_users.members.first().user
        
        form_data = {
            'name': 'Test Experiment',
            'type': 'llm',
            'participant_access_level': ParticipantAccessLevel.DENY_LIST,
            'participant_allowlist': ['alice@example.com'],  # Should be cleared
            'participant_denylist': ['bob@example.com'],
            'llm_provider': experiment.llm_provider.id if experiment.llm_provider else None,
            'llm_provider_model': experiment.llm_provider_model.id if experiment.llm_provider_model else None,
            'prompt_text': 'Test prompt',
        }
        
        form = ExperimentForm(request, data=form_data, instance=experiment)
        if form.is_valid():
            cleaned = form.cleaned_data
            assert cleaned['participant_access_level'] == ParticipantAccessLevel.DENY_LIST
            assert cleaned['participant_allowlist'] == []
            assert 'bob@example.com' in cleaned['participant_denylist']


@pytest.mark.django_db()
class TestEndToEndFlow:
    """Test complete flow from form to database to access check"""

    def test_complete_deny_list_flow(self, team_with_users):
        """Test creating and using an experiment with deny list"""
        # Create experiment with deny list
        experiment = ExperimentFactory(
            team=team_with_users,
            participant_access_level=ParticipantAccessLevel.DENY_LIST,
            participant_denylist=["blocked@example.com", "+1234567890"],
            participant_allowlist=[]
        )
        
        # Verify it's not public
        assert experiment.is_public is False
        
        # Verify blocked participants can't access
        assert experiment.is_participant_allowed("blocked@example.com") is False
        assert experiment.is_participant_allowed("+1234567890") is False
        
        # Verify others can access
        assert experiment.is_participant_allowed("allowed@example.com") is True
        assert experiment.is_participant_allowed("+9876543210") is True
        
        # Verify database state
        assert experiment.participant_access_level == ParticipantAccessLevel.DENY_LIST
        assert "blocked@example.com" in experiment.participant_denylist
        assert len(experiment.participant_allowlist) == 0

    def test_switching_access_levels(self, experiment):
        """Test switching between different access levels"""
        # Start with OPEN
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.participant_denylist = []
        experiment.save()
        assert experiment.is_public is True
        
        # Switch to ALLOW_LIST
        experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
        experiment.participant_allowlist = ["alice@example.com"]
        experiment.participant_denylist = []
        experiment.save()
        experiment.refresh_from_db()
        assert experiment.is_public is False
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("bob@example.com") is False
        
        # Switch to DENY_LIST
        experiment.participant_access_level = ParticipantAccessLevel.DENY_LIST
        experiment.participant_allowlist = []
        experiment.participant_denylist = ["alice@example.com"]
        experiment.save()
        experiment.refresh_from_db()
        assert experiment.is_public is False
        assert experiment.is_participant_allowed("alice@example.com") is False
        assert experiment.is_participant_allowed("bob@example.com") is True
        
        # Switch back to OPEN
        experiment.participant_access_level = ParticipantAccessLevel.OPEN
        experiment.participant_allowlist = []
        experiment.participant_denylist = []
        experiment.save()
        experiment.refresh_from_db()
        assert experiment.is_public is True
        assert experiment.is_participant_allowed("alice@example.com") is True
        assert experiment.is_participant_allowed("bob@example.com") is True
