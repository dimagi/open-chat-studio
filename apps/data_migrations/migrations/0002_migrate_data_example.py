"""
Example Django migration showing how to use the idempotent migration system.

This migration demonstrates how to run custom data migrations from within
Django's migration framework while ensuring they only run once across
all environments.

Usage:
    python manage.py migrate data_migrations
"""

from django.db import migrations

from apps.data_migrations.utils.migrations import (
    check_migration_in_django_migration,
    mark_migration_in_django_migration,
)

# Unique identifier for this migration
MIGRATION_NAME = "example_data_migration_2024_11_21"


def migrate_data(apps, schema_editor):
    """
    Example migration function.

    Important: Use apps.get_model() for all model access, never direct imports.
    This ensures the migration works correctly with the historical model state.
    """
    # Check if migration has already been applied
    if check_migration_in_django_migration(apps, MIGRATION_NAME):
        print(f"  Skipping '{MIGRATION_NAME}' - already applied")
        return

    # Get the model using apps.get_model()
    # Example: CustomUser = apps.get_model('users', 'CustomUser')

    # Perform migration logic here
    # Example:
    # users = CustomUser.objects.filter(first_name='')
    # for user in users:
    #     user.first_name = 'Default'
    #     user.save()

    print(f"  Running migration '{MIGRATION_NAME}'...")

    # Your migration logic goes here
    # ...

    # Mark migration as applied
    mark_migration_in_django_migration(apps, MIGRATION_NAME)
    print(f"  Completed migration '{MIGRATION_NAME}'")


def reverse_migration(apps, schema_editor):
    """
    Reverse the migration by removing the tracking record.

    Note: This only removes the tracking record. You may need to add
    additional logic to reverse the actual data changes.
    """
    CustomMigration = apps.get_model("data_migrations", "CustomMigration")
    CustomMigration.objects.filter(name=MIGRATION_NAME).delete()
    print(f"  Reversed migration '{MIGRATION_NAME}'")


class Migration(migrations.Migration):
    dependencies = [
        ("data_migrations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(migrate_data, reverse_migration),
    ]
