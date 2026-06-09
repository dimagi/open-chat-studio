from django.apps import AppConfig


class CostTrackingConfig(AppConfig):
    name = "apps.cost_tracking"
    label = "cost_tracking"

    def ready(self):
        from apps.cost_tracking import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
