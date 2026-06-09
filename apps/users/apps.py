from django.apps import AppConfig


class UserConfig(AppConfig):
    name = "apps.users"
    label = "users"

    def ready(self):
        from . import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
