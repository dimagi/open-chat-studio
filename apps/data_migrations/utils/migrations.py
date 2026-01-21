from contextlib import ContextDecorator, ExitStack

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

    def __init__(self, command_name, elidable=False):
        self.command_name = command_name
        self.elidable = elidable

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        call_command(self.command_name)

    def describe(self):
        return f"Run data migration: {self.command_name}"

    def deconstruct(self):
        kwargs = {}
        if self.elidable:
            kwargs["elidable"] = self.elidable
        return (
            self.__class__.__qualname__,
            [self.command_name],
            kwargs,
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


@transaction.atomic()
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


@transaction.atomic()
def update_migration_timestamp(name: str) -> None:
    """
    Update the applied_at timestamp for an existing migration to the current time.

    Used when re-running a migration with --force flag to track when it was last executed.

    Args:
        name: Unique migration identifier
    """
    from django.utils import timezone

    CustomMigration.objects.filter(name=name).update(applied_at=timezone.now())


class run_once(ContextDecorator):
    """
    Context manager and decorator to ensure code runs only once.

    Can be used as a decorator or context manager. Marks the migration as applied on success.
    If `atomic` is True, it also wraps the code in a transaction.

    Args:
        name: Unique migration identifier

    Example as decorator:
        @run_once("my_migration_2024_11_21")
        def my_migration_function():
            # Migration logic here
            pass

    Example as context manager:
        with run_once("my_migration_2024_11_21"):
            # Migration logic here
            pass
    """

    def __init__(self, name: str, *, atomic=True):
        self.name = name
        self.should_run = False
        self.atomic = atomic
        self.stack = ExitStack()

    def __enter__(self):
        self.stack.__enter__()
        if self.atomic:
            self.stack.enter_context(transaction.atomic())

        if is_migration_applied(self.name):
            self.should_run = False
        else:
            self.should_run = True

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self.should_run:
            mark_migration_applied(self.name)

        return self.stack.__exit__(exc_type, exc_val, exc_tb)


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


@transaction.atomic()
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
