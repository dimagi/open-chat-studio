from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.pipelines.migrations.utils.strip_node_data import strip_node_data_from_pipelines
from apps.pipelines.models import Node, Pipeline


class Command(IdempotentCommand):
    help = (
        "Drop the nodes key from Pipeline.data, leaving edges (and viewport) only (ADR-0048), "
        "after backfilling each node's position onto the Node row's position columns. "
        "Run by migration pipelines.0030_strip_node_data. Idempotent and safe to rerun; "
        "pipelines whose blobs have no backing Node row are skipped and logged."
    )
    migration_name = "strip_pipeline_node_data_2026_07_27"
    # Batches are independent and commit on their own; migration 0030 is non-atomic to match.
    atomic = False

    def perform_migration(self, dry_run=False):
        if dry_run:
            self.stdout.write("The helper writes as it iterates, so there is nothing to preview.")
            return

        strip_node_data_from_pipelines(Pipeline, Node, progress_callback=self._report_progress)
        self.stdout.write(self.style.SUCCESS("Stripped node data from pipeline data."))

    def _report_progress(self, processed, total):
        self.stdout.write(f"Processed {processed}/{total} pipelines")
