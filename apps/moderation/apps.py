from django.apps import AppConfig


class ModerationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.moderation"
    label = "moderation"

    def ready(self):
        from . import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
