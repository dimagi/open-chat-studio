from django.apps import AppConfig


class TeamConfig(AppConfig):
    name = "apps.teams"
    label = "teams"

    def ready(self):
        from . import signals
