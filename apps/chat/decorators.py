import logging
from functools import wraps

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification

logger = logging.getLogger("ocs.notifications")


def notify_on_delivery_failure(context: str):
    """
    Decorator that catches exceptions from channel message delivery methods and creates a failure notification.
    This decorator is intended to be used inside a Channel class with experiment and experiment_channel attributes

    Args:
        context: Description of what was being sent (e.g., "text message", "voice message")

    The decorator logs the exception and creates a notification before re-raising.
    """

    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception as e:
                logger.exception(e)
                platform_title = self.experiment_channel.platform_enum.title()
                create_notification(
                    title=f"Message Delivery Failed for {self.experiment.name}",
                    message=f"An error occurred while delivering a {context} to the user via {platform_title}",
                    level=LevelChoices.ERROR,
                    slug="message-delivery-failed",
                    team=self.experiment.team,
                    permissions=["experiments.view_experimentsession"],
                    event_data={
                        "bot_id": self.experiment.id,
                        "platform": self.experiment_channel.platform,
                        "context": context,
                    },
                )
                raise

        return wrapper

    return decorator
