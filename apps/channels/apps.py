from django.apps import AppConfig


class BotChannelsAppConfig(AppConfig):
    name = "apps.channels"
    label = "bot_channels"

    def ready(self):
        # Register signal handlers
        from . import signals  # noqa: F401
