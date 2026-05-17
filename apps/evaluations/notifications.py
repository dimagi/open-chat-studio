import logging

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification
from apps.utils.decorators import silence_exceptions

logger = logging.getLogger("ocs.evaluations")


@silence_exceptions(logger, log_message="Failed to create auto-population disable notification")
def auto_population_rule_disabled_notification(rule: DatasetAutoPopulationRule, reason: str) -> None:
    """Notify team admins that an auto-population rule has been disabled."""
    create_notification(
        title="Auto-population rule disabled",
        message=(
            f"The auto-population rule for dataset '{rule.dataset.name}' "
            f"(source: {rule.source_experiment.name}) was automatically disabled: {reason}."
        ),
        level=LevelChoices.WARNING,
        team=rule.team,
        slug="evaluations-auto-population-disabled",
        event_data={"rule_id": rule.id, "dataset_id": rule.dataset_id},
        permissions=["evaluations.change_evaluationdataset"],
        links={"View dataset": rule.dataset.get_absolute_url()},
    )
