"""
Example implementation of IdempotentCommand.

This demonstrates how to use the IdempotentCommand base class
for creating data migrations that run exactly once.

Usage:
    python manage.py migrate_user_data           # Normal execution
    python manage.py migrate_user_data --dry-run  # Preview changes
    python manage.py migrate_user_data --force    # Force re-run
"""

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.users.models import CustomUser


class Command(IdempotentCommand):
    help = "Example migration: Update user data (splits name into first_name and last_name)"

    migration_name = "migrate_user_data_v1_2024_11_21"

    def perform_migration(self, dry_run=False):
        """
        Example migration logic: Split user's name field into first_name and last_name.

        This is a demonstration - modify the logic for your actual use case.
        """
        # Query relevant data - find users with a name but missing first_name
        users = CustomUser.objects.filter(
            first_name="",
        ).exclude(
            username="",
        )

        count = users.count()
        self.stdout.write(f"Found {count} users to process")

        if count == 0:
            return 0

        if dry_run:
            # Show preview of first 5 items
            self.stdout.write("Preview of first 5 users:")
            for user in users[:5]:
                self.stdout.write(f"  - {user.username}: would update first_name")
            return count

        # Perform actual updates
        updated = 0
        for user in users:
            # Example: use username as first_name if not set
            # Modify this logic for your actual migration needs
            if not user.first_name and user.username:
                parts = user.username.split("@")[0].split(".")
                user.first_name = parts[0].title() if parts else user.username
                if len(parts) > 1:
                    user.last_name = parts[-1].title()
                user.save(update_fields=["first_name", "last_name"])
                updated += 1

                if updated % 100 == 0:
                    self.stdout.write(f"  Processed {updated} users...")

        self.stdout.write(f"Updated {updated} users")
        return updated
