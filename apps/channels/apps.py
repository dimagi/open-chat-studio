from django.apps import AppConfig


class BotChannelsAppConfig(AppConfig):
    name = "apps.channels"
    label = "bot_channels"

    def ready(self):
        # Register signal handlers
        from anymail.signals import inbound  # noqa: PLC0415 - lazy: signal hookup belongs in ready()

        from . import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
        from .channels_v2.email_channel import email_inbound_handler  # noqa: PLC0415 - lazy: hookup in ready()

        inbound.connect(email_inbound_handler)
