from django.apps import AppConfig


class ExperimentAppConfig(AppConfig):
    name = "apps.experiments"
    label = "experiments"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals
