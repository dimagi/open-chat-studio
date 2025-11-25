from django.apps import AppConfig


class DataMigrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.data_migrations"
    label = "data_migrations"
