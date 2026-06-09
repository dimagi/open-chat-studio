from django.apps import AppConfig


class CostTrackingConfig(AppConfig):
    name = "apps.cost_tracking"
    label = "cost_tracking"

    def ready(self):
        """Import signals so the post_save / post_delete receivers register."""
        from apps.cost_tracking import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
