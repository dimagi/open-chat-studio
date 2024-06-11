from django.apps import AppConfig
from django.conf import settings


class SlackConfig(AppConfig):
    name = "apps.slack"
    label = "slack"
    _registered_listeners = False

    def ready(self):
        if not self._registered_listeners and settings.SLACK_CLIENT_ID and settings.SLACK_CLIENT_SECRET:
            # don't do this in tests
            from apps.slack.slack_listeners import register_listeners

            register_listeners()

            # avoid double registration
            self._registered_listeners = True
