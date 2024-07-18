from django.apps import AppConfig


class APIConfig(AppConfig):
    name = "apps.api"
    label = "api"

    def ready(self):
        from . import schema  # noqa
