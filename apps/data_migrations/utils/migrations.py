from functools import wraps

from django.core.management import call_command
from django.db import migrations, transaction
from django.db.migrations.operations.base import OperationCategory

from apps.data_migrations.models import CustomMigration


class RunDataMigration(migrations.operations.base.Operation):
    """
    Custom migration operation to run a data migration management command.

    Example:
        from apps.data_migrations.utils.migrations import RunDataMigration

        class Migration(migrations.Migration):
            operations = [
                RunDataMigration("my_migration"),
            ]
    """

    reversible = False
    reduces_to_sql = False
    category = OperationCategory.PYTHON

    def __init__(self, command_name):
        self.command_name = command_name

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        call_command(self.command_name)

    def describe(self):
        return f"Run data migration: {self.command_name}"

    def deconstruct(self):
        return (
            self.__class__.__qualname__,
            [self.command_name],
            {},
        )


def is_migration_applied(name: str) -> bool:
    """
    Check if a custom migration has been applied.

    Args:
        name: Unique migration identifier

    Returns:
        Boolean indicating if migration exists in database
    """
    return CustomMigration.objects.filter(name=name).exists()


def mark_migration_applied(name: str) -> CustomMigration:
    """
    Mark a custom migration as applied.

    Uses get_or_create() to handle race conditions when multiple
    processes might try to mark the same migration simultaneously.

    Args:
        name: Unique migration identifier

    Returns:
        CustomMigration instance (created or existing)
    """
    migration, _created = CustomMigration.objects.get_or_create(name=name)
    return migration


def run_once(name: str):
    """
    Decorator to ensure a function runs only once.

    Wraps the function in an atomic transaction and checks if the
    migration has already been applied before running.

    Args:
        name: Unique migration identifier

    Returns:
        Decorator function

    Example:
        @run_once("my_migration_2024_11_21")
        def my_migration_function():
            # Migration logic here
            pass
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with transaction.atomic():
                if is_migration_applied(name):
                    return False

                result = func(*args, **kwargs)
                mark_migration_applied(name)
                return result

        return wrapper

    return decorator


def check_migration_in_django_migration(apps, name: str) -> bool:
    """
    Check migration status from within a Django migration.

    Must use apps.get_model() instead of direct import because
    the model may not be fully loaded during migrations.

    Args:
        apps: Django apps registry from migration
        name: Migration identifier

    Returns:
        Boolean indicating if migration has been applied
    """
    CustomMigrationModel = apps.get_model("data_migrations", "CustomMigration")
    return CustomMigrationModel.objects.filter(name=name).exists()


def mark_migration_in_django_migration(apps, name: str) -> None:
    """
    Mark migration as applied from within a Django migration.

    Must use apps.get_model() instead of direct import because
    the model may not be fully loaded during migrations.

    Args:
        apps: Django apps registry from migration
        name: Migration identifier
    """
    CustomMigrationModel = apps.get_model("data_migrations", "CustomMigration")
    CustomMigrationModel.objects.get_or_create(name=name)
