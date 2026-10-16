from django.apps import AppConfig


class ModerationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.moderation"
    label = "moderation"

    def ready(self):
        from apps.teams.views.team_settings import register_settings_section  # noqa: PLC0415 - needs loaded apps

        from . import signals  # noqa: F401, PLC0415 - lazy: signal registration belongs in ready()
        from .views import SETTINGS_SECTION  # noqa: PLC0415 - needs loaded apps

        register_settings_section(section=SETTINGS_SECTION, after="notifications")
