from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Max

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession
from apps.trace.models import Trace


class Command(IdempotentCommand):
    help = "Backfill platform, experiment_versions, and last_activity_at fields for ExperimentSession"
    migration_name = "backfill_session_fields_2025_11_26"

    def perform_migration(self, dry_run=False):
        batch_size = 1000

        self.stdout.write("Aggregating trace data...")

        # Get trace data aggregated by session in one query
        trace_data = {
            item["session_id"]: {
                "versions": sorted(set(v for v in item["versions"] if v is not None)),
                "last_activity": item["last_activity"],
            }
            for item in Trace.objects.filter(session__isnull=False)
            .values("session_id")
            .annotate(
                versions=ArrayAgg("experiment_version_number"),
                last_activity=Max("timestamp"),
            )
        }

        self.stdout.write(f"Found trace data for {len(trace_data)} sessions")

        # Process sessions in batches using iterator
        sessions = (
            ExperimentSession.objects.select_related("experiment_channel", "participant")
            .only("id", "experiment_channel__platform", "participant__platform")
            .iterator(chunk_size=batch_size)
        )

        batch = []
        total_updated = 0

        for session in sessions:
            # Set platform (use the value, not the label)
            if session.experiment_channel:
                session.platform = session.experiment_channel.platform
            elif session.participant:
                session.platform = session.participant.platform

            # Set experiment_versions and last_activity_at from trace data
            if session.id in trace_data:
                session.experiment_versions = trace_data[session.id]["versions"]
                session.last_activity_at = trace_data[session.id]["last_activity"]

            batch.append(session)

            if len(batch) >= batch_size:
                ExperimentSession.objects.bulk_update(
                    batch,
                    ["platform", "experiment_versions", "last_activity_at"],
                    batch_size=batch_size,
                )
                total_updated += len(batch)
                self.stdout.write(f"Updated {total_updated} sessions...")
                batch = []

        # Update remaining sessions
        if batch:
            ExperimentSession.objects.bulk_update(
                batch,
                ["platform", "experiment_versions", "last_activity_at"],
                batch_size=batch_size,
            )
            total_updated += len(batch)

        self.stdout.write(self.style.SUCCESS(f"Successfully updated {total_updated} sessions"))
