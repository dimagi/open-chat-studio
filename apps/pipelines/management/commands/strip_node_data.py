from django.core.management.base import BaseCommand

from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)
from apps.pipelines.models import Node, Pipeline


class Command(BaseCommand):
    help = (
        "Strip embedded node content from Pipeline.data, leaving layout only (ADR-0046), "
        "and backfill each node's position onto the Node row's position columns. "
        "Idempotent and safe to rerun; pipelines whose blobs have no backing Node row are "
        "skipped and logged."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Reverse: rebuild the embedded node blobs from the Node rows (needed by pre-ADR-0046 code).",
        )

    def handle(self, *args, **options):
        if options["rebuild"]:
            rebuild_node_data_in_pipelines(Pipeline, Node)
            self.stdout.write(self.style.SUCCESS("Rebuilt node data in pipeline data."))
        else:
            strip_node_data_from_pipelines(Pipeline, Node)
            self.stdout.write(self.style.SUCCESS("Stripped node data from pipeline data."))
