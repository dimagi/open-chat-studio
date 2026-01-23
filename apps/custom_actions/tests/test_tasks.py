import pytest
import responses

from apps.custom_actions.models import HealthCheckStatus
from apps.custom_actions.tasks import check_single_custom_action_health
from apps.utils.factories.custom_actions import CustomActionFactory


@pytest.mark.django_db()
class TestHealthCheckTask:
    """Tests for the health check task."""

    @pytest.mark.parametrize(
        ("response_kwargs", "expected_status", "description"),
        [
            (
                {"status": 200, "json": {"status": "ok"}},
                HealthCheckStatus.UP,
                "successful health check",
            ),
            (
                {"status": 500, "json": {"error": "Internal server error"}},
                HealthCheckStatus.DOWN,
                "bad status code",
            ),
        ],
    )
    @responses.activate
    def test_health_check_status(self, response_kwargs, expected_status, description, team_with_users):
        """Test health check with various response scenarios."""
        custom_action_with_health = CustomActionFactory(
            team=team_with_users,
            healthcheck_path="/health",
            server_url="https://example.com",
        )

        # Mock the health check response
        responses.add(responses.GET, "https://example.com/health", **response_kwargs)

        # Run the health check task
        check_single_custom_action_health(custom_action_with_health.id)

        # Refresh from database
        custom_action_with_health.refresh_from_db()

        # Verify status was updated
        assert custom_action_with_health.health_status == expected_status
        assert custom_action_with_health.last_health_check is not None

    def test_health_check_no_endpoint(self, team_with_users):
        """Test that action without health endpoint is skipped."""
        action = CustomActionFactory(team=team_with_users)
        initial_status = action.health_status

        # Run the health check task
        check_single_custom_action_health(action.id)

        # Refresh from database
        action.refresh_from_db()

        # Verify status was NOT updated
        assert action.health_status == initial_status
        assert action.last_health_check is None
