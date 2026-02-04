import logging

import httpx
from celery.app import shared_task
from django.utils import timezone

from apps.custom_actions.models import CustomAction, HealthCheckStatus
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification

logger = logging.getLogger("ocs.custom_actions")

# Timeout for health check requests (in seconds)
HEALTH_CHECK_TIMEOUT = 5


@shared_task(ignore_result=True)
def check_all_custom_actions_health():
    """Periodic task to check health of all custom actions with health endpoints configured."""
    custom_actions = CustomAction.objects.exclude(healthcheck_path__isnull=True).exclude(healthcheck_path="")

    for action in custom_actions:
        check_single_custom_action_health.delay(action.id)

    logger.info(f"Scheduled health checks for {custom_actions.count()} custom actions")


@shared_task(ignore_result=True)
def check_single_custom_action_health(action_id: int):
    """Check health of a single custom action.

    Args:
        action_id: The ID of the CustomAction to check
    """
    try:
        action = CustomAction.objects.get(id=action_id)
    except CustomAction.DoesNotExist:
        logger.error(f"CustomAction with id {action_id} not found")
        return

    if not action.health_endpoint:
        logger.warning(f"CustomAction {action.name} (id={action_id}) has no health endpoint configured")
        return

    try:
        response = httpx.get(action.health_endpoint, timeout=HEALTH_CHECK_TIMEOUT)

        # Consider 2xx status codes as "up"
        if 200 <= response.status_code < 300:
            new_status = HealthCheckStatus.UP
            logger.info(f"Health check passed for {action.name}: {response.status_code}")
        else:
            new_status = HealthCheckStatus.DOWN
            logger.warning(f"Health check failed for {action.name}: {response.status_code}")

    except httpx.RequestError as e:
        new_status = HealthCheckStatus.DOWN
        logger.warning(f"Health check error for {action.name}: {str(e)}")

    # Notify team members if status changed to DOWN from a non-DOWN state
    should_notify = new_status == HealthCheckStatus.DOWN and action.health_status != HealthCheckStatus.DOWN

    # Update the action's health status
    action.health_status = new_status
    action.last_health_check = timezone.now()
    action.save(update_fields=["health_status", "last_health_check"])

    # Send notification only on DOWN transition
    if should_notify:
        create_notification(
            title="Custom Action is down",
            message=f"The custom action '{action.name}' is currently unreachable at its health endpoint.",
            level=LevelChoices.ERROR,
            team=action.team,
            slug="custom-action-health-check",
            event_data={"action_id": action.id, "status": action.health_status},
            permissions=["custom_actions.change_customaction"],
        )
