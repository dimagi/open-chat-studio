from datetime import datetime

import dictdiffer
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.teams.models import Team
from apps.trace.models import Trace


class Command(IdempotentCommand):
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

        # Subquery: get the participant_data of the next trace in the same session.
        # Note: if two traces share the same timestamp, ordering is non-deterministic.
        next_trace_in_session = (
            Trace.objects.filter(
                session_id=OuterRef("session_id"),
                timestamp__gt=OuterRef("timestamp"),
            )
            .order_by("timestamp")
            .values("participant_data")[:1]
        )

        # Annotate each trace with the next trace's participant_data
        annotated_qs = base_qs.annotate(
            next_participant_data=Subquery(next_trace_in_session),
        ).order_by("timestamp")

        total_count = annotated_qs.count()
        self.stdout.write(f"Found {total_count} traces to evaluate")

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No traces to process"))
            return

        # Pass 1: Process traces that have a next trace in the same session
        updated_count = self._process_same_session_traces(annotated_qs, dry_run)

        # Pass 2: Process traces that are the last in their session (next_participant_data is NULL)
        last_in_session_qs = base_qs.filter(
            id__in=Subquery(
                base_qs.filter(
                    session_id=OuterRef("session_id"),
                )
                .order_by("-timestamp")
                .values("id")[:1]
            ),
        )
        updated_count += self._process_last_in_session_traces(last_in_session_qs, dry_run)

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated_count} traces total"))

    def _process_same_session_traces(self, annotated_qs, dry_run):
        """Process traces that have a next trace in the same session."""
        # Filter to traces where next_participant_data exists and differs from current
        traces_with_next = annotated_qs.exclude(next_participant_data__isnull=True)

        total = traces_with_next.count()
        self.stdout.write(f"Pass 1: {total} traces with a next trace in the same session")

        updated_count = 0
        processed = 0
        last_id = 0

        while True:
            batch = list(
                traces_with_next.filter(id__gt=last_id)
                .order_by("id")
                .values_list("id", "participant_data", "next_participant_data")[: self.batch_size]
            )

            if not batch:
                break

            last_id = batch[-1][0]

            traces_to_update = []
            for trace_id, current_pd, next_pd in batch:
                processed += 1
                if current_pd == next_pd:
                    continue

                diff = list(dictdiffer.diff(current_pd or {}, next_pd or {}))
                if diff:
                    traces_to_update.append(Trace(id=trace_id, participant_data_diff=diff))

            if traces_to_update:
                if not dry_run:
                    Trace.objects.bulk_update(traces_to_update, ["participant_data_diff"], batch_size=self.batch_size)
                updated_count += len(traces_to_update)

            if processed % (self.batch_size * 5) == 0 or len(batch) < self.batch_size:
                action = "Would update" if dry_run else "Updated"
                self.stdout.write(f"  Pass 1: {action} {updated_count} so far (processed {processed}/{total})")

        return updated_count

    def _process_last_in_session_traces(self, last_in_session_qs, dry_run):
        """Process traces that are the last in their session.

        For each such trace, find the end-state participant data by looking at:
        1. The first trace of the participant's next session (same experiment)
        2. The global ParticipantData record if no next session exists
        """
        total = last_in_session_qs.count()
        self.stdout.write(f"Pass 2: {total} traces that are last in their session")

        if total == 0:
            return 0

        updated_count = 0
        processed = 0
        last_id = 0

        while True:
            batch = list(
                last_in_session_qs.filter(id__gt=last_id)
                .order_by("id")
                .values_list("id", "participant_data", "participant_id", "experiment_id", "session__created_at")[
                    : self.batch_size
                ]
            )

            if not batch:
                break

            last_id = batch[-1][0]

            # Collect participant+experiment pairs we need to resolve
            participant_experiment_pairs = {(t[2], t[3]) for t in batch}

            # Prefetch: for each participant+experiment, get the next session's first trace participant_data
            next_session_lookup = self._build_next_session_lookup(participant_experiment_pairs, batch)

            # Prefetch: global ParticipantData for fallback
            global_pd_lookup = self._build_global_pd_lookup(participant_experiment_pairs)

            traces_to_update = []
            for trace_id, current_pd, participant_id, experiment_id, session_created_at in batch:
                processed += 1

                end_pd = self._resolve_end_participant_data(
                    participant_id, experiment_id, session_created_at, next_session_lookup, global_pd_lookup
                )

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
                self.stdout.write(f"  Pass 2: {action} {updated_count} so far (processed {processed}/{total})")

        return updated_count

    def _build_next_session_lookup(self, participant_experiment_pairs, batch):
        """Build a lookup of (participant_id, experiment_id) -> [(session_created_at, first_trace_pd)].

        For each participant+experiment pair, fetch all sessions ordered by creation date,
        then for each session get the first trace's participant_data.
        """
        # Compute the earliest session date per pair from the batch
        earliest_dates = {}
        for t in batch:
            key = (t[2], t[3])
            if key not in earliest_dates or t[4] < earliest_dates[key]:
                earliest_dates[key] = t[4]

        # Build a single Q filter for all pairs' next sessions
        session_q = Q()
        for (participant_id, experiment_id), earliest_date in earliest_dates.items():
            session_q |= Q(
                participant_id=participant_id,
                experiment_id=experiment_id,
                created_at__gt=earliest_date,
            )

        if not session_q:
            return {}

        # Single query: fetch all next sessions for all pairs
        next_sessions = list(
            ExperimentSession.objects.filter(session_q)
            .order_by("created_at")
            .values_list("id", "participant_id", "experiment_id", "created_at")
        )

        if not next_sessions:
            return {}

        next_session_ids = [s[0] for s in next_sessions]

        # Single query: fetch the first trace's participant_data for each next session
        first_traces = dict(
            Trace.objects.filter(session_id__in=next_session_ids)
            .order_by("session_id", "timestamp")
            .distinct("session_id")
            .values_list("session_id", "participant_data")
        )

        # Build lookup grouped by (participant_id, experiment_id)
        lookup = {}
        for session_id, participant_id, experiment_id, created_at in next_sessions:
            key = (participant_id, experiment_id)
            pd = first_traces.get(session_id)
            if pd is not None:
                lookup.setdefault(key, []).append((created_at, pd))

        return lookup

    def _build_global_pd_lookup(self, participant_experiment_pairs):
        """Build a lookup of (participant_id, experiment_id) -> participant data dict."""
        q_filter = Q()
        for participant_id, experiment_id in participant_experiment_pairs:
            q_filter |= Q(participant_id=participant_id, experiment_id=experiment_id)

        lookup = {}
        if q_filter:
            for pd in ParticipantData.objects.filter(q_filter):
                lookup[(pd.participant_id, pd.experiment_id)] = pd.data or {}
        return lookup

    def _resolve_end_participant_data(
        self, participant_id, experiment_id, session_created_at, next_session_lookup, global_pd_lookup
    ):
        """Resolve the end-state participant data for a trace that's last in its session.

        Priority:
        1. First trace of the next session (same participant + experiment)
        2. Global ParticipantData record
        """
        key = (participant_id, experiment_id)
        next_sessions = next_session_lookup.get(key, [])

        # Find the first session created after the current session
        for created_at, pd in next_sessions:
            if created_at > session_created_at:
                return pd

        # Fallback to global ParticipantData
        return global_pd_lookup.get(key)
