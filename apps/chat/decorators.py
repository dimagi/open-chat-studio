import logging
from functools import wraps

from apps.ocs_notifications.notifications import message_delivery_failure_notification

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
                message_delivery_failure_notification(
                    self.experiment,
                    session=self._experiment_session,
                    platform=self.experiment_channel.platform,
                    platform_title=platform_title,
                    context=context,
                )
                raise

        return wrapper

    return decorator
