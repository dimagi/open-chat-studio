from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Max, Q

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession
from apps.trace.models import Trace


class Command(IdempotentCommand):
    help = "Backfill platform, experiment_versions, and last_activity_at fields for ExperimentSession"
    migration_name = "backfill_session_fields_2025_11_26"
    atomic = False
    disable_audit = True

    def perform_migration(self, dry_run=False):
        batch_size = 1000

        # Get all session IDs that need processing
        # We'll track progress by ID to avoid infinite loops when some fields can't be populated
        sessions_needing_update = ExperimentSession.objects.filter(
            Q(platform__isnull=True) | Q(experiment_versions__isnull=True) | Q(last_activity_at__isnull=True)
        ).values_list("id", flat=True)

        total_count = sessions_needing_update.count()
        self.stdout.write(f"Found {total_count} sessions that need processing")

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No sessions need processing"))
            return

        # Get the range of IDs to process
        min_id = ExperimentSession.objects.order_by("id").values_list("id", flat=True).first()
        max_id = ExperimentSession.objects.order_by("-id").values_list("id", flat=True).first()

        if min_id is None or max_id is None:
            self.stdout.write(self.style.SUCCESS("No sessions found"))
            return

        total_updated = 0
        current_id = min_id

        # Process all sessions in ID order, regardless of current null status
        # This ensures we make progress through the ID space and don't loop infinitely
        while current_id <= max_id:
            # Get next batch of sessions in ID range
            batch_sessions = list(
                ExperimentSession.objects.filter(id__gte=current_id, id__lt=current_id + batch_size)
                .select_related("experiment_channel", "participant")
                .only(
                    "id",
                    "platform",
                    "experiment_versions",
                    "last_activity_at",
                    "experiment_channel__platform",
                    "participant__platform",
                )
            )

            if not batch_sessions:
                current_id += batch_size
                continue

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

            # Progress reporting
            if sessions_to_update or current_id % (batch_size * 10) == 0:
                action = "Would update" if dry_run else "Updated"
                progress_pct = ((current_id - min_id) / (max_id - min_id)) * 100 if max_id > min_id else 100
                self.stdout.write(f"{action} {total_updated}/{total_count} sessions ({progress_pct:.1f}%)...")

            # Move to next batch
            current_id += batch_size

        if dry_run:
            msg = f"Would update {total_updated} sessions"
        else:
            msg = f"Successfully updated {total_updated} sessions"
        self.stdout.write(self.style.SUCCESS(msg))
