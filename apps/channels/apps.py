from django.apps import AppConfig


class BotChannelsAppConfig(AppConfig):
    name = "apps.channels"
    label = "bot_channels"

    def ready(self):
        # Register signal handlers
        from anymail.signals import inbound  # noqa: PLC0415

        from . import signals  # noqa: F401, PLC0415
        from .email import email_inbound_handler  # noqa: PLC0415

        inbound.connect(email_inbound_handler)
