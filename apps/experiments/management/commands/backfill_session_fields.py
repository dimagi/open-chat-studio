from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Max, Q

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession
from apps.trace.models import Trace


class Command(IdempotentCommand):
    help = "Backfill platform, experiment_versions, and last_activity_at fields for ExperimentSession"
    migration_name = "backfill_session_fields_2025_11_26"
    atomic = False

    def perform_migration(self, dry_run=False):
        batch_size = 1000

        # Filter to only sessions that need processing
        # A session needs processing if any of the fields are null
        sessions_to_process = ExperimentSession.objects.filter(
            Q(platform__isnull=True) | Q(experiment_versions__isnull=True) | Q(last_activity_at__isnull=True)
        ).select_related("experiment_channel", "participant")

        total_count = sessions_to_process.count()
        self.stdout.write(f"Found {total_count} sessions to process")

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No sessions need processing"))
            return

        total_updated = 0

        # Process in batches - keep querying until no more sessions need processing
        while True:
            # Get next batch of sessions that need processing
            batch_sessions = list(
                sessions_to_process.select_related("experiment_channel", "participant")
                .only("id", "experiment_channel__platform", "participant__platform")
                .order_by("id")[:batch_size]
            )

            if not batch_sessions:
                break

            # Get trace data only for this batch of sessions
            session_ids = [s.id for s in batch_sessions]
            trace_data = {
                item["session_id"]: {
                    "versions": sorted(set(v for v in item["versions"] if v is not None)),
                    "last_activity": item["last_activity"],
                }
                for item in Trace.objects.filter(session_id__in=session_ids)
                .values("session_id")
                .annotate(
                    versions=ArrayAgg("experiment_version_number"),
                    last_activity=Max("timestamp"),
                )
            }

            # Update sessions in this batch
            sessions_to_update = []
            for session in batch_sessions:
                modified = False

                # Set platform if null
                if session.platform is None:
                    if session.experiment_channel:
                        session.platform = session.experiment_channel.platform
                        modified = True
                    elif session.participant:
                        session.platform = session.participant.platform
                        modified = True

                # Set experiment_versions and last_activity_at from trace data if null
                if session.id in trace_data:
                    if session.experiment_versions is None:
                        session.experiment_versions = trace_data[session.id]["versions"]
                        modified = True
                    if session.last_activity_at is None:
                        session.last_activity_at = trace_data[session.id]["last_activity"]
                        modified = True

                if modified:
                    sessions_to_update.append(session)

            # Bulk update this batch
            if sessions_to_update:
                if not dry_run:
                    ExperimentSession.objects.bulk_update(
                        sessions_to_update,
                        ["platform", "experiment_versions", "last_activity_at"],
                        batch_size=batch_size,
                    )
                total_updated += len(sessions_to_update)
                action = "Would update" if dry_run else "Updated"
                progress_pct = (total_updated / total_count) * 100 if total_count > 0 else 100
                self.stdout.write(f"{action} {total_updated}/{total_count} sessions ({progress_pct:.1f}%)...")

        if dry_run:
            msg = f"Would update {total_updated} sessions"
        else:
            msg = f"Successfully updated {total_updated} sessions"
        self.stdout.write(self.style.SUCCESS(msg))
