from functools import wraps

from django.db import transaction

from apps.data_migrations.models import CustomMigration


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
