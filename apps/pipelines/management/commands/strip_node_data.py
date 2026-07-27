from django.core.management.base import BaseCommand, CommandError

from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)
from apps.pipelines.models import Node, Pipeline
from apps.teams.models import Team


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
        parser.add_argument(
            "--team",
            help="Limit to a single team, by slug.",
        )

    def handle(self, *args, **options):
        team = None
        if options["team"]:
            try:
                team = Team.objects.get(slug=options["team"])
            except Team.DoesNotExist:
                raise CommandError(f"Team '{options['team']}' does not exist.") from None

        if options["rebuild"]:
            rebuild_node_data_in_pipelines(Pipeline, Node, team=team)
            self.stdout.write(self.style.SUCCESS("Rebuilt node data in pipeline data."))
        else:
            strip_node_data_from_pipelines(Pipeline, Node, team=team, progress_callback=self._report_progress)
            self.stdout.write(self.style.SUCCESS("Stripped node data from pipeline data."))

    def _report_progress(self, processed, total):
        self.stdout.write(f"Processed {processed}/{total} pipelines")
