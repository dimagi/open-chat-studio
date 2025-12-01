from importlib import import_module

from allauth.account import app_settings
from django.apps import AppConfig
from django.conf import settings
from django.db import migrations
from django.db.models.functions import Lower


class UserConfig(AppConfig):
    name = "apps.users"
    label = "users"

    def ready(self):
        from . import signals  # noqa  F401

        migration_module = import_module("allauth.account.migrations.0006_emailaddress_lower")
        migration_module.Migration.operations = patched_migration_operations()


def patched_migration_operations():
    """Patch the migration to pass the `audit_action` kwarg to the user query `update` method"""

    def forwards(apps, schema_editor):
        from field_audit.models import AuditAction

        EmailAddress = apps.get_model("account.EmailAddress")
        User = apps.get_model(settings.AUTH_USER_MODEL)
        EmailAddress.objects.all().exclude(email=Lower("email")).update(email=Lower("email"))
        email_field = app_settings.USER_MODEL_EMAIL_FIELD
        if email_field:
            # add the audit_action kwarg
            User.objects.all().exclude(**{email_field: Lower(email_field)}).update(
                **{email_field: Lower(email_field)}, audit_action=AuditAction.IGNORE
            )

    return [migrations.RunPython(forwards, migrations.RunPython.noop)]
