from django.apps import AppConfig


class TeamConfig(AppConfig):
    name = "apps.teams"
    label = "teams"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals
