from unittest.mock import patch

import pytest

from apps.custom_actions.models import HealthCheckStatus
from apps.custom_actions.tasks import check_all_custom_actions_health, check_single_custom_action_health
from apps.utils.factories.custom_actions import CustomActionFactory, CustomActionOperationFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


def _attach_to_bot(action, team, *, experiment_archived=False, node_archived=False, node_is_version=False):
    """Reference the action from a chatbot's pipeline node; the defaults make a bot that can call it."""
    pipeline = PipelineFactory.create(team=team)
    working_version = NodeFactory.create(pipeline=pipeline) if node_is_version else None
    node = NodeFactory.create(pipeline=pipeline, is_archived=node_archived, working_version=working_version)
    CustomActionOperationFactory.create(custom_action=action, node=node)
    ExperimentFactory.create(team=team, pipeline=pipeline, is_archived=experiment_archived)


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
        custom_action_with_health = CustomActionFactory.create(
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
        action = CustomActionFactory.create(team=team_with_users)
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
        """Only actions with a health path that a live chatbot can call are scheduled."""
        action_with_health_1 = CustomActionFactory.create(
            team=team_with_users,
            healthcheck_path="/health",
            server_url="https://example.com",
        )
        _attach_to_bot(action_with_health_1, team_with_users)
        action_with_health_2 = CustomActionFactory.create(
            team=team_with_users,
            healthcheck_path="/healthz",
            server_url="https://example2.com",
        )
        _attach_to_bot(action_with_health_2, team_with_users)
        no_health_path = CustomActionFactory.create(team=team_with_users, healthcheck_path="")
        _attach_to_bot(no_health_path, team_with_users)
        archived_bot_only = CustomActionFactory.create(team=team_with_users, healthcheck_path="/health")
        _attach_to_bot(archived_bot_only, team_with_users, experiment_archived=True)
        CustomActionFactory.create(team=team_with_users, healthcheck_path="/health")  # no chatbot

        # Run the periodic task
        check_all_custom_actions_health()

        # Verify delay was called only for actions with health paths
        assert mock_delay.call_count == 2
        mock_delay.assert_any_call(action_with_health_1.id)
        mock_delay.assert_any_call(action_with_health_2.id)


@pytest.mark.django_db()
class TestHealthCheckFailureNotification:
    @pytest.mark.parametrize(
        ("bot", "allowed_operations", "expected_calls"),
        [
            pytest.param({}, ["weather_get"], 1, id="live-bot"),
            pytest.param({"experiment_archived": True}, ["weather_get"], 0, id="archived-bot"),
            pytest.param({"node_archived": True}, ["weather_get"], 0, id="node-removed-from-bot"),
            pytest.param({}, [], 0, id="operation-disabled-on-working-node"),
            pytest.param({"node_is_version": True}, [], 1, id="operation-disabled-but-published"),
        ],
    )
    @patch("apps.custom_actions.tasks.custom_action_health_check_failure_notification")
    def test_notifies_only_for_actions_a_live_bot_can_call(
        self, mock_notify, bot, allowed_operations, expected_calls, team_with_users, httpx_mock
    ):
        action = CustomActionFactory.create(
            team=team_with_users,
            healthcheck_path="/health",
            server_url="https://example.com",
            allowed_operations=allowed_operations,
        )
        _attach_to_bot(action, team_with_users, **bot)
        httpx_mock.add_response(url="https://example.com/health", status_code=500)

        check_single_custom_action_health(action.id)

        action.refresh_from_db()
        assert action.health_status == HealthCheckStatus.DOWN
        assert mock_notify.call_count == expected_calls

    @patch("apps.custom_actions.tasks.custom_action_health_check_failure_notification")
    def test_a_direct_check_of_an_unused_action_records_status_without_alerting(
        self, mock_notify, team_with_users, httpx_mock
    ):
        """The create view and the check-health button call the task for actions no chatbot uses yet."""
        action = CustomActionFactory.create(
            team=team_with_users, healthcheck_path="/health", server_url="https://example.com"
        )
        httpx_mock.add_response(url="https://example.com/health", status_code=500)

        check_single_custom_action_health(action.id)

        action.refresh_from_db()
        assert action.health_status == HealthCheckStatus.DOWN
        assert action.last_health_check is not None
        mock_notify.assert_not_called()
