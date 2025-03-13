from django.apps import AppConfig


class SsoConfig(AppConfig):
    name = "apps.sso"
    label = "sso"

    def ready(self):
        from . import signals  # noqa
