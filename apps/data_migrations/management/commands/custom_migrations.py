"""
Management command to manage custom migrations.

Usage:
    python manage.py custom_migrations list                    # List all applied migrations
    python manage.py custom_migrations mark <name>             # Mark a migration as applied
    python manage.py custom_migrations unmark <name>           # Unmark a migration (delete record)
"""

from django.core.management.base import BaseCommand, CommandError

from apps.data_migrations.models import CustomMigration


class Command(BaseCommand):
    help = "Manage custom migrations: list, mark, or unmark migrations"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", help="Action to perform")

        # List subcommand
        list_parser = subparsers.add_parser("list", help="List all applied custom migrations")
        list_parser.add_argument(
            "--name",
            type=str,
            help="Filter migrations by name (substring match)",
        )

        # Mark subcommand
        mark_parser = subparsers.add_parser("mark", help="Mark a migration as applied")
        mark_parser.add_argument("name", type=str, help="Name of the migration to mark")

        # Unmark subcommand
        unmark_parser = subparsers.add_parser("unmark", help="Unmark a migration (delete the record)")
        unmark_parser.add_argument("name", type=str, help="Name of the migration to unmark")

    def handle(self, *args, **options):
        action = options.get("action")

        if not action:
            self.print_help("manage.py", "custom_migrations")
            return

        if action == "list":
            self.handle_list(options)
        elif action == "mark":
            self.handle_mark(options)
        elif action == "unmark":
            self.handle_unmark(options)
        else:
            raise CommandError(f"Unknown action: {action}")

    def handle_list(self, options):
        """List all applied custom migrations."""
        queryset = CustomMigration.objects.all()

        # Filter by name if provided
        name_filter = options.get("name")
        if name_filter:
            queryset = queryset.filter(name__icontains=name_filter)

        migrations = queryset.order_by("-applied_at")
        count = migrations.count()

        if count == 0:
            if name_filter:
                self.stdout.write(f"No migrations found matching '{name_filter}'")
            else:
                self.stdout.write("No custom migrations have been applied")
            return

        self.stdout.write(f"Found {count} custom migration(s):\n")
        self.stdout.write(f"{'Name':<50} {'Applied At':<25}")
        self.stdout.write("-" * 75)

        for migration in migrations:
            applied_at = migration.applied_at.strftime("%Y-%m-%d %H:%M:%S")
            self.stdout.write(f"{migration.name:<50} {applied_at:<25}")

    def handle_mark(self, options):
        """Mark a migration as applied."""
        name = options["name"]

        migration, created = CustomMigration.objects.get_or_create(name=name)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Migration '{name}' marked as applied"))
        else:
            self.stdout.write(self.style.WARNING(f"Migration '{name}' was already marked as applied"))

    def handle_unmark(self, options):
        """Unmark a migration (delete the record)."""
        name = options["name"]

        try:
            migration = CustomMigration.objects.get(name=name)
            migration.delete()
            self.stdout.write(self.style.SUCCESS(f"Migration '{name}' unmarked (record deleted)"))
        except CustomMigration.DoesNotExist:
            raise CommandError(f"Migration '{name}' not found") from None
