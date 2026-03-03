import contextlib
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q
from field_audit import disable_audit

from apps.data_migrations.utils.migrations import (
    is_migration_applied,
    mark_migration_applied,
    run_once,
    update_migration_timestamp,
)


def get_affected_teams_data(db_model) -> dict:
    """Return affected resources per team for a given LlmProviderModel.

    Returns a dict of the form:
        {team_id: {"chatbots": {name: url}, "pipelines": {name: url}, "assistants": {name: url}}}
    """
    from apps.assistants.models import OpenAiAssistant
    from apps.experiments.models import Experiment
    from apps.utils.deletion import get_related_pipelines_queryset

    teams_data = defaultdict(lambda: {"chatbots": {}, "pipelines": {}, "assistants": {}})

    related_pipeline_nodes = get_related_pipelines_queryset(db_model, "llm_provider_model_id")
    nodes_by_pipeline = defaultdict(list)
    pipelines = []
    for node in related_pipeline_nodes.select_related("pipeline").all():
        pipelines.append(node.pipeline)
        nodes_by_pipeline[node.pipeline_id].append(node)

    referenced_experiments = Experiment.objects.filter(pipeline_id__in=list(nodes_by_pipeline)).filter(
        Q(working_version__isnull=True) | Q(is_default_version=True)
    )
    referenced_pipeline_ids = {exp.pipeline_id for exp in referenced_experiments}
    unreferenced_pipelines = [p for p in pipelines if p.id not in referenced_pipeline_ids]

    referenced_assistants = OpenAiAssistant.objects.filter(llm_provider_model=db_model, working_version__isnull=True)

    for exp in referenced_experiments:
        teams_data[exp.team_id]["chatbots"][exp.name] = exp.get_absolute_url()
    for pipeline in unreferenced_pipelines:
        teams_data[pipeline.team_id]["pipelines"][pipeline.name] = pipeline.get_absolute_url()
    for assistant in referenced_assistants:
        teams_data[assistant.team_id]["assistants"][assistant.name] = assistant.get_absolute_url()

    return dict(teams_data)


class IdempotentCommand(BaseCommand):
    """
    Abstract base class for management commands that should run only once.

    Subclasses must define:
        - migration_name: Unique identifier for this migration
        - atomic: Set to False to disable atomic migration
        - disable_audit: Set to True to disable model auditing for this migration
        - perform_migration(): Method containing the actual migration logic

    Command options:
        --dry-run: Preview changes without applying them or marking as complete
        --force: Re-run even if already applied
        --fake: Mark migration as complete without running it

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
        parser.add_argument(
            "--fake",
            action="store_true",
            help="Mark migration as complete without running it",
        )

    def handle(self, *args, **options):
        # Validate that migration_name is defined
        if not self.migration_name:
            raise NotImplementedError("Subclass must define 'migration_name' attribute")

        self.verbosity = options["verbosity"]
        force = options.get("force", False)
        dry_run = options.get("dry_run", False)
        fake = options.get("fake", False)

        # Handle fake mode: mark as complete without running
        if fake:
            if is_migration_applied(self.migration_name):
                self.stdout.write(self.style.WARNING(f"Migration '{self.migration_name}' is already marked as applied"))
            else:
                mark_migration_applied(self.migration_name)
                self.stdout.write(self.style.SUCCESS(f"Migration '{self.migration_name}' marked as applied (fake)"))
            return

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

            # Update timestamp when re-running with --force
            if force and not migration_context.should_run:
                update_migration_timestamp(self.migration_name)
                self.stdout.write("Migration timestamp updated to current time")

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
