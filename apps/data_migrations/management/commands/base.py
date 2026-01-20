import contextlib

from django.core.management.base import BaseCommand
from field_audit import disable_audit

from apps.data_migrations.utils.migrations import is_migration_applied, run_once


class IdempotentCommand(BaseCommand):
    """
    Abstract base class for management commands that should run only once.

    Subclasses must define:
        - migration_name: Unique identifier for this migration
        - atomic: Set to False to disable atomic migration
        - disable_audit: Set to True to disable model auditing for this migration
        - perform_migration(): Method containing the actual migration logic

    Example:
        class Command(IdempotentCommand):
            help = 'Migrate user data to new format'
            migration_name = 'migrate_user_data_v2_2024_11_21'

            def perform_migration(self, dry_run=False):
                # Migration logic here
                pass
    """

    # Subclasses must override this
    migration_name: str = ""
    atomic = True
    disable_audit = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force re-run even if migration was already applied",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without applying them",
        )

    def handle(self, *args, **options):
        # Validate that migration_name is defined
        if not self.migration_name:
            raise NotImplementedError("Subclass must define 'migration_name' attribute")

        self.verbosity = options["verbosity"]
        force = options.get("force", False)
        dry_run = options.get("dry_run", False)

        # Check if migration already applied (unless force flag is set)
        if not force and is_migration_applied(self.migration_name):
            self.stdout.write(
                self.style.WARNING(
                    f"Migration '{self.migration_name}' has already been applied.\n"
                    "Use --force to re-run or --dry-run to preview."
                )
            )
            return

        # Handle dry-run mode
        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN MODE - No changes will be applied"))
            self.perform_migration(dry_run=True)
            self.stdout.write(self.style.NOTICE("DRY RUN COMPLETE - No changes were applied"))
            return

        # Execute migration
        self.stdout.write(f"Starting migration: {self.migration_name}")
        try:
            with run_once(self.migration_name, atomic=self.atomic) as migration_context:
                if not migration_context.should_run and not force:
                    self.stdout.write(
                        self.style.WARNING(f"Migration '{self.migration_name}' was already applied during execution")
                    )
                    return

                audit_context = disable_audit() if self.disable_audit else contextlib.nullcontext()
                with audit_context:
                    result = self.perform_migration(dry_run=False)

            self.stdout.write(self.style.SUCCESS(f"Migration '{self.migration_name}' completed successfully"))

            if result is not None:
                self.stdout.write(f"Result: {result}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Migration '{self.migration_name}' failed: {e}"))
            raise

    def perform_migration(self, dry_run=False):
        """
        Override this method with actual migration logic.

        Args:
            dry_run: If True, only preview changes without applying them

        Returns:
            Optional return value (e.g., count of records affected)
        """
        raise NotImplementedError("Subclass must implement perform_migration()")
