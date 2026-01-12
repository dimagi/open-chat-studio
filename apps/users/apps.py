from allauth.account import app_settings
from django.apps import AppConfig
from django.conf import settings
from django.db import migrations
from django.db.models.functions import Lower
from django.db.models.signals import pre_migrate


class UserConfig(AppConfig):
    name = "apps.users"
    label = "users"

    def ready(self):
        from . import signals  # noqa  F401

        self._patch_allauth_migration()

    def _patch_allauth_migration(self):
        """Patch allauth 0006_emailaddress_lower to work with field_audit."""

        def forwards_with_audit(apps, schema_editor):
            from field_audit.models import AuditAction

            EmailAddress = apps.get_model("account.EmailAddress")
            User = apps.get_model(settings.AUTH_USER_MODEL)
            EmailAddress.objects.all().exclude(email=Lower("email")).update(email=Lower("email"))
            email_field = app_settings.USER_MODEL_EMAIL_FIELD
            if email_field:
                User.objects.all().exclude(**{email_field: Lower(email_field)}).update(
                    **{email_field: Lower(email_field)},
                    audit_action=AuditAction.IGNORE,
                )

        def before_migrations(**kwargs):
            if plan := kwargs.get("plan"):
                for migration, applied in plan:
                    if not applied and migration.app_label == "account" and migration.name == "0006_emailaddress_lower":
                        migration.operations = [migrations.RunPython(forwards_with_audit, migrations.RunPython.noop)]

        pre_migrate.connect(before_migrations)
