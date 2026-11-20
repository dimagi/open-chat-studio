"""Prepare, complete, or reverse the relation-backed Node resource cutover."""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.pipelines.migrations.utils.strip_node_resource_params import (
    restore_node_resource_params,
    strip_node_resource_params,
)
from apps.pipelines.models import NODE_RESOURCE_PARAM_CUTOVER_LOCK_ID, NodeResourceCutoverState


class Command(BaseCommand):
    """Control the legacy JSON bridge around rolling deployments."""

    help = "Prepare or complete the relation-backed node resource cutover."

    def add_arguments(self, parser):
        """Register the requested cutover phase and its read-only validation option."""
        parser.add_argument("action", choices=("prepare", "cutover", "restore"))
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate all node resources without changing state or node params.",
        )

    def handle(self, *args, action: str, dry_run: bool = False, **options):
        """Run one compatibility phase after normal schema migrations have completed."""
        if dry_run and action != "cutover":
            raise CommandError("--dry-run is available only for the cutover action")
        if action == "prepare":
            self._prepare()
        elif action == "cutover":
            self._cutover(dry_run=dry_run)
        else:
            self._restore()

    def _prepare(self) -> None:
        """Enable legacy JSON mirrors before the first relation-aware rolling deploy."""
        with transaction.atomic():
            self._lock_writers_exclusively()
            state = NodeResourceCutoverState.objects.select_for_update().get(pk=1)
            if state.cleanup_completed_at is None:
                state.legacy_json_compatibility_enabled = True
                state.save(update_fields=["legacy_json_compatibility_enabled"])
        self.stdout.write(self.style.SUCCESS("Legacy JSON compatibility is ready for deployment."))

    def _cutover(self, *, dry_run: bool) -> None:
        """Validate and remove legacy JSON keys after all relation-aware services are live."""
        if dry_run:
            with connection.schema_editor() as schema_editor:
                strip_node_resource_params(apps, schema_editor, dry_run=True)
            self.stdout.write(self.style.SUCCESS("Cutover validation passed; no data was changed."))
            return

        with transaction.atomic():
            self._lock_writers_exclusively()
            state = NodeResourceCutoverState.objects.select_for_update().get(pk=1)
            if state.cleanup_completed_at is not None:
                self.stdout.write(self.style.SUCCESS("Node resource params were already cut over."))
                return
            state.legacy_json_compatibility_enabled = False
            state.save(update_fields=["legacy_json_compatibility_enabled"])

        with connection.schema_editor() as schema_editor:
            strip_node_resource_params(apps, schema_editor)

        with transaction.atomic():
            state = NodeResourceCutoverState.objects.select_for_update().get(pk=1)
            state.cleanup_completed_at = timezone.now()
            state.save(update_fields=["cleanup_completed_at"])
        self.stdout.write(self.style.SUCCESS("Node resource params cutover completed."))

    def _restore(self) -> None:
        """Restore relation values to JSON before rolling back to a legacy application release."""
        with transaction.atomic():
            self._lock_writers_exclusively()
            state = NodeResourceCutoverState.objects.select_for_update().get(pk=1)
            state.legacy_json_compatibility_enabled = True
            state.cleanup_completed_at = None
            state.save(update_fields=["legacy_json_compatibility_enabled", "cleanup_completed_at"])
        with connection.schema_editor() as schema_editor:
            restore_node_resource_params(apps, schema_editor)
        self.stdout.write(self.style.SUCCESS("Node resource params were restored from relations."))

    @staticmethod
    def _lock_writers_exclusively() -> None:
        """Wait for in-flight relation writes before changing the compatibility state."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [NODE_RESOURCE_PARAM_CUTOVER_LOCK_ID])
