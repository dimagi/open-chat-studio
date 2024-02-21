from django.apps import AppConfig


class ExperimentAppConfig(AppConfig):
    name = "apps.experiments"
    label = "experiments"

    def ready(self):
        from . import signals  # noqa  F401
