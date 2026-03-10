from datetime import datetime

import dictdiffer
from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, When
from django.db.models.fields.json import JSONField
from django.utils import timezone

from apps.data_migrations.management.commands.base import BaseCommand
from apps.experiments.models import ParticipantData
from apps.teams.models import Team
from apps.trace.models import Trace


class Command(BaseCommand):
    # NOTE: Backfilled diffs are an approximation. Real-time diffs capture exactly what changed
    # during a single trace's execution (before vs after). Backfilled diffs compare this trace's
    # participant_data snapshot to the next trace's snapshot, which may include changes that were
    # manually made by admins
    help = "Backfill participant_data_diff for traces by comparing participant data between consecutive traces"
    migration_name = "backfill_participant_data_diff_2026_03_09"
    atomic = False
    disable_audit = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("team_slug", type=str, help="The team slug to process")
        parser.add_argument(
            "since_date",
            type=str,
            help="Earliest date to backfill from (ISO format: YYYY-MM-DD)",
        )
        parser.add_argument(
            "--experiment-id",
            type=int,
            default=None,
            help="Optional experiment ID to filter by",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of traces to process per batch (default: 1000)",
        )

    def handle(self, *args, **options):
        self.team_slug = options["team_slug"]
        self.since_date = options["since_date"]
        self.experiment_id = options.get("experiment_id")
        self.batch_size = options.get("batch_size", 1000)

        try:
            self.team = Team.objects.get(slug=self.team_slug)
        except Team.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Team with slug '{self.team_slug}' not found"))
            return

        try:
            self.since_datetime = timezone.make_aware(datetime.fromisoformat(self.since_date))
        except ValueError:
            self.stderr.write(self.style.ERROR(f"Invalid date format: '{self.since_date}'. Use YYYY-MM-DD"))
            return

        super().handle(*args, **options)

    def perform_migration(self, dry_run=False):
        base_qs = (
            Trace.objects.filter(
                team=self.team,
                timestamp__gte=self.since_datetime,
            )
            .exclude(
                session__isnull=True,
            )
            .exclude(
                participant__isnull=True,
            )
        )

        if self.experiment_id:
            base_qs = base_qs.filter(experiment_id=self.experiment_id)

        # Subquery: participant_data of the next trace in the same session
        next_in_session = (
            Trace.objects.filter(
                session_id=OuterRef("session_id"),
                timestamp__gt=OuterRef("timestamp"),
            )
            .order_by("timestamp")
            .values("participant_data")[:1]
        )

        # Subquery: participant_data of the first trace in the next session (same participant + experiment)
        next_session_first_trace = (
            Trace.objects.filter(
                participant_id=OuterRef("participant_id"),
                experiment_id=OuterRef("experiment_id"),
                session__created_at__gt=OuterRef("session__created_at"),
            )
            .order_by("session__created_at", "timestamp")
            .values("participant_data")[:1]
        )

        # Existence checks to drive the Case/When
        has_next_in_session = Exists(
            Trace.objects.filter(
                session_id=OuterRef("session_id"),
                timestamp__gt=OuterRef("timestamp"),
            )
        )

        has_next_session_trace = Exists(
            Trace.objects.filter(
                participant_id=OuterRef("participant_id"),
                experiment_id=OuterRef("experiment_id"),
                session__created_at__gt=OuterRef("session__created_at"),
            )
        )

        # ParticipantData.data is encrypted (bytea), so it can't be mixed with jsonb
        # in a Case/When. Annotate only Trace-based sources; handle the global
        # ParticipantData fallback in Python below.
        annotated_qs = base_qs.annotate(
            next_participant_data=Case(
                When(condition=has_next_in_session, then=Subquery(next_in_session)),
                When(condition=has_next_session_trace, then=Subquery(next_session_first_trace)),
                output_field=JSONField(),
            ),
        ).order_by("id")

        total_count = annotated_qs.count()
        self.stdout.write(f"Found {total_count} traces to evaluate")

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No traces to process"))
            return

        # Process traces that have a next trace (same session or next session)
        traces_with_next = annotated_qs.exclude(next_participant_data__isnull=True).exclude(
            participant_data=F("next_participant_data")
        )
        updated_count = self._process_batched(traces_with_next, total_count, dry_run)

        # Process traces that need the global ParticipantData fallback
        # (no next trace in same session and no next session with traces)
        fallback_qs = annotated_qs.filter(next_participant_data__isnull=True)
        updated_count += self._process_global_pd_fallback(fallback_qs, total_count, dry_run)

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated_count} traces total"))

    def _process_batched(self, qs, total_count, dry_run):
        updated_count = 0
        processed = 0
        last_id = 0

        while True:
            batch = list(
                qs.filter(id__gt=last_id).values_list("id", "participant_data", "next_participant_data")[
                    : self.batch_size
                ]
            )

            if not batch:
                break

            last_id = batch[-1][0]

            traces_to_update = []
            for trace_id, current_pd, next_pd in batch:
                processed += 1
                diff = list(dictdiffer.diff(current_pd or {}, next_pd or {}))
                if diff:
                    traces_to_update.append(Trace(id=trace_id, participant_data_diff=diff))

            if traces_to_update:
                if not dry_run:
                    Trace.objects.bulk_update(traces_to_update, ["participant_data_diff"], batch_size=self.batch_size)
                updated_count += len(traces_to_update)

            if processed % (self.batch_size * 5) == 0 or len(batch) < self.batch_size:
                action = "Would update" if dry_run else "Updated"
                self.stdout.write(f"  {action} {updated_count} so far (processed {processed}/{total_count})")

        return updated_count

    def _process_global_pd_fallback(self, qs, total_count, dry_run):
        updated_count = 0
        processed = 0
        last_id = 0

        while True:
            batch = list(
                qs.filter(id__gt=last_id)
                .order_by("id")
                .values_list("id", "participant_data", "participant_id", "experiment_id")[: self.batch_size]
            )

            if not batch:
                break

            last_id = batch[-1][0]

            # Batch-fetch global ParticipantData for all participant+experiment pairs in this batch
            q_filter = Q()
            for _, _, participant_id, experiment_id in batch:
                q_filter |= Q(participant_id=participant_id, experiment_id=experiment_id)

            global_pd_lookup = {}
            if q_filter:
                for pd in ParticipantData.objects.filter(q_filter):
                    global_pd_lookup[(pd.participant_id, pd.experiment_id)] = pd.data or {}

            traces_to_update = []
            for trace_id, current_pd, participant_id, experiment_id in batch:
                processed += 1
                end_pd = global_pd_lookup.get((participant_id, experiment_id))
                if end_pd is None or current_pd == end_pd:
                    continue

                diff = list(dictdiffer.diff(current_pd or {}, end_pd))
                if diff:
                    traces_to_update.append(Trace(id=trace_id, participant_data_diff=diff))

            if traces_to_update:
                if not dry_run:
                    Trace.objects.bulk_update(traces_to_update, ["participant_data_diff"], batch_size=self.batch_size)
                updated_count += len(traces_to_update)

            if processed % (self.batch_size * 5) == 0 or len(batch) < self.batch_size:
                action = "Would update" if dry_run else "Updated"
                self.stdout.write(f"  {action} {updated_count} so far (processed {processed}/{total_count})")

        return updated_count
