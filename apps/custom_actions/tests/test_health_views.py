import pytest
from django.urls import reverse

from apps.custom_actions.forms import CustomActionForm
from apps.custom_actions.models import CustomAction
from apps.utils.factories.custom_actions import CustomActionFactory


@pytest.mark.django_db()
class TestCustomActionForm:
    """Tests for CustomActionForm with health endpoint field."""
    
    def test_form_includes_health_endpoint_field(self, rf, team_with_users):
        """Test that the form includes the health_endpoint field."""
        request = rf.get("/")
        request.team = team_with_users
        
        form = CustomActionForm(request=request)
        assert "health_endpoint" in form.fields
    
    def test_form_health_endpoint_optional(self, rf, team_with_users):
        """Test that health_endpoint is optional in the form."""
        request = rf.get("/")
        request.team = team_with_users
        
        form = CustomActionForm(request=request)
        assert form.fields["health_endpoint"].required is False
    
    def test_form_saves_health_endpoint(self, rf, team_with_users):
        """Test that the form saves the health_endpoint value."""
        from apps.utils.factories.custom_actions import ACTION_SCHEMA
        
        request = rf.get("/")
        request.team = team_with_users
        
        data = {
            "name": "Test Action",
            "description": "Test description",
            "server_url": "https://api.test.com",
            "health_endpoint": "https://api.test.com/health",
            "api_schema": ACTION_SCHEMA,
            "prompt": "Test prompt",
        }
        
        form = CustomActionForm(request=request, data=data)
        assert form.is_valid(), form.errors
        
        action = form.save(commit=False)
        action.team = team_with_users
        action.save()

        assert action.health_endpoint == "https://api.test.com/health"


@pytest.mark.django_db()
class TestCheckHealthView:
    """Tests for the CheckCustomActionHealth view."""

    def test_check_health_triggers_task(self, client, team_with_users):
        """Test that the check health endpoint triggers the task."""
        user = team_with_users.members.first()
        client.force_login(user)

        action = CustomActionFactory(
            team=team_with_users,
            health_endpoint="https://example.com/health"
        )

        url = reverse("custom_actions:check_health", args=[team_with_users.slug, action.pk])
        response = client.post(url)

        assert response.status_code == 200
        assert "Checking..." in response.content.decode()

    def test_check_health_no_endpoint(self, client, team_with_users):
        """Test checking health on action without endpoint."""
        user = team_with_users.members.first()
        client.force_login(user)

        action = CustomActionFactory(team=team_with_users, health_endpoint=None)
        
        url = reverse("custom_actions:check_health", args=[team_with_users.slug, action.pk])
        response = client.post(url)
        
        assert response.status_code == 200
        assert "No health endpoint configured" in response.content.decode()
    
    def test_check_health_requires_permission(self, client, team_with_users):
        """Test that check health requires view permission."""
        # Create user without login
        action = CustomActionFactory(team=team_with_users)
        
        url = reverse("custom_actions:check_health", args=[team_with_users.slug, action.pk])
        response = client.post(url)
        
        # Should redirect to login
        assert response.status_code == 302
    
    def test_check_health_wrong_team(self, client, team_with_users):
        """Test that users can't check health for actions from other teams."""
        from apps.utils.factories.team import TeamWithUsersFactory
        
        user = team_with_users.members.first()
        client.force_login(user)
        
        # Create action in different team
        other_team = TeamWithUsersFactory.create()
        action = CustomActionFactory(team=other_team)
        
        url = reverse("custom_actions:check_health", args=[team_with_users.slug, action.pk])
        response = client.post(url)
        
        # Should return 404 since action doesn't belong to the team
        assert response.status_code == 404
