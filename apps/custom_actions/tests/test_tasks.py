from unittest.mock import patch

import pytest

from apps.custom_actions.models import HealthCheckStatus
from apps.custom_actions.tasks import check_all_custom_actions_health, check_single_custom_action_health
from apps.utils.factories.custom_actions import CustomActionFactory


@pytest.mark.django_db()
class TestHealthCheckTask:
    """Tests for the health check task."""

    @pytest.mark.parametrize(
        ("status_code", "expected_status", "description"),
        [
            (200, HealthCheckStatus.UP, "successful health check"),
            (500, HealthCheckStatus.DOWN, "bad status code"),
        ],
    )
    def test_health_check_status(self, status_code, expected_status, description, team_with_users, httpx_mock):
        """Test health check with various response scenarios."""
        custom_action_with_health = CustomActionFactory(
            team=team_with_users,
            healthcheck_path="/health",
            server_url="https://example.com",
        )

        # Mock the health check response
        httpx_mock.add_response(url="https://example.com/health", status_code=status_code, json={"status": "ok"})

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

    @patch("apps.custom_actions.tasks.check_single_custom_action_health.delay")
    def test_check_all_custom_actions_health(self, mock_delay, team_with_users):
        """Test that custom actions with health paths are loaded."""
        # Create custom actions: some with health paths, some without
        action_with_health_1 = CustomActionFactory(
            team=team_with_users,
            healthcheck_path="/health",
            server_url="https://example.com",
        )
        action_with_health_2 = CustomActionFactory(
            team=team_with_users,
            healthcheck_path="/healthz",
            server_url="https://example2.com",
        )
        CustomActionFactory(
            team=team_with_users,
            healthcheck_path="",
        )

        # Run the periodic task
        check_all_custom_actions_health()

        # Verify delay was called only for actions with health paths
        assert mock_delay.call_count == 2
        mock_delay.assert_any_call(action_with_health_1.id)
        mock_delay.assert_any_call(action_with_health_2.id)
