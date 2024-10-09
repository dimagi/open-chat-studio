import logging

from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created
from django.dispatch import receiver

log = logging.getLogger(__name__)


class SlackConfig(AppConfig):
    name = "apps.slack"
    label = "slack"

    def ready(self):
        if settings.SLACK_ENABLED:
            # don't do this in tests
            from apps.slack.slack_listeners import register_listeners

            @receiver(connection_created)
            def initial_connection_to_db(sender, **kwargs):
                """This is a hack to ensure that we only register the slack listeners
                after the database is ready. This is necessary because during the registration
                process we query the DB to get the current Site object."""
                try:
                    register_listeners()
                except Exception:
                    log.exception("Error registering slack listeners")
