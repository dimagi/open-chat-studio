import pytest
import responses
from django.utils import timezone

from apps.custom_actions.models import CustomAction
from apps.custom_actions.tasks import check_single_custom_action_health
from apps.utils.factories.custom_actions import CustomActionFactory


@pytest.fixture()
def custom_action_with_health(team_with_users):
    """Create a custom action with a health endpoint."""
    return CustomActionFactory(
        team=team_with_users,
        health_endpoint="https://example.com/health"
    )


@pytest.mark.django_db()
class TestHealthCheckTask:
    """Tests for the health check task."""
    
    @responses.activate
    def test_health_check_success(self, custom_action_with_health):
        """Test that a successful health check updates status to 'up'."""
        # Mock a successful health check response
        responses.add(
            responses.GET,
            "https://example.com/health",
            status=200,
            json={"status": "ok"}
        )

        # Run the health check task
        check_single_custom_action_health(custom_action_with_health.id)

        # Refresh from database
        custom_action_with_health.refresh_from_db()

        # Verify status was updated
        assert custom_action_with_health.health_status == "up"
        assert custom_action_with_health.last_health_check is not None

    @responses.activate
    def test_health_check_failure_bad_status(self, custom_action_with_health):
        """Test that a failed health check (bad status code) updates status to 'down'."""
        # Mock a failed health check response
        responses.add(
            responses.GET,
            "https://example.com/health",
            status=500,
            json={"error": "Internal server error"}
        )

        # Run the health check task
        check_single_custom_action_health(custom_action_with_health.id)

        # Refresh from database
        custom_action_with_health.refresh_from_db()

        # Verify status was updated
        assert custom_action_with_health.health_status == "down"
        assert custom_action_with_health.last_health_check is not None

    @responses.activate
    def test_health_check_failure_connection_error(self, custom_action_with_health):
        """Test that a connection error updates status to 'down'."""
        # Mock a connection error
        responses.add(
            responses.GET,
            "https://example.com/health",
            body=Exception("Connection refused")
        )

        # Run the health check task
        check_single_custom_action_health(custom_action_with_health.id)

        # Refresh from database
        custom_action_with_health.refresh_from_db()
        
        # Verify status was updated
        assert custom_action_with_health.health_status == "down"
        assert custom_action_with_health.last_health_check is not None
    
    def test_health_check_no_endpoint(self, team_with_users):
        """Test that action without health endpoint is skipped."""
        action = CustomActionFactory(team=team_with_users, health_endpoint=None)
        initial_status = action.health_status
        
        # Run the health check task
        check_single_custom_action_health(action.id)
        
        # Refresh from database
        action.refresh_from_db()
        
        # Verify status was NOT updated
        assert action.health_status == initial_status
        assert action.last_health_check is None
    
    def test_health_check_nonexistent_action(self):
        """Test that checking a non-existent action doesn't raise an error."""
        # This should not raise an exception
        check_single_custom_action_health(99999)


@pytest.mark.django_db()
class TestCustomActionModel:
    """Tests for CustomAction model health fields."""
    
    def test_default_health_status(self, team_with_users):
        """Test that new actions have default health status of 'unknown'."""
        action = CustomActionFactory(team=team_with_users)
        assert action.health_status == "unknown"
        assert action.last_health_check is None
    
    def test_health_endpoint_optional(self, team_with_users):
        """Test that health_endpoint is optional."""
        action = CustomActionFactory(team=team_with_users, health_endpoint=None)
        assert action.health_endpoint is None
        
        action2 = CustomActionFactory(team=team_with_users, health_endpoint="")
        assert action2.health_endpoint == ""
    
    def test_health_status_choices(self, team_with_users):
        """Test that health_status accepts valid choices."""
        action = CustomActionFactory(team=team_with_users)
        
        for status in ["unknown", "up", "down"]:
            action.health_status = status
            action.save()
            action.refresh_from_db()
            assert action.health_status == status
