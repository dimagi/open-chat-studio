from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.chat.models import ChatMessage, ChatMessageType
from apps.teams.models import Team
from apps.trace.models import Trace


class Command(BaseCommand):
    # Traces created before the ad hoc bot message paths linked their AI message to the
    # trace (see #3440) have output_message=NULL even though the message exists and
    # carries the trace id in its metadata. This command restores the trace -> message
    # link from the message metadata.
    help = "Backfill Trace.output_message for traces whose AI message references them via metadata trace_info"

    def add_arguments(self, parser):
        parser.add_argument(
            "--team-slug",
            type=str,
            default=None,
            help="Optional team slug to restrict the backfill to",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of traces to process per batch (default: 500)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without applying them",
        )

    def handle(self, *args, **options):
        team_slug = options.get("team_slug")
        self.batch_size = options.get("batch_size", 500)
        dry_run = options.get("dry_run", False)

        traces = Trace.objects.filter(output_message__isnull=True, session__isnull=False)
        if team_slug:
            try:
                team = Team.objects.get(slug=team_slug)
            except Team.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Team with slug '{team_slug}' not found"))
                return
            traces = traces.filter(team=team)

        total_count = traces.count()
        self.stdout.write(f"Found {total_count} traces without an output message")

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN MODE - No changes will be applied"))

        updated_count = self._perform_backfill(traces.order_by("id"), total_count, dry_run)

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated_count} traces total"))

    def _perform_backfill(self, qs, total_count, dry_run):
        updated_count = 0
        processed = 0
        last_id = 0

        while True:
            batch = list(qs.filter(id__gt=last_id).values_list("id", "session__chat_id")[: self.batch_size])
            if not batch:
                break

            last_id = batch[-1][0]
            processed += len(batch)
            updated_count += self._update_batch(batch, dry_run)

            if processed % (self.batch_size * 5) == 0 or len(batch) < self.batch_size:
                action = "Would update" if dry_run else "Updated"
                self.stdout.write(f"  {action} {updated_count} so far (processed {processed}/{total_count})")

        return updated_count

    def _update_batch(self, batch, dry_run) -> int:
        """Link each trace in the batch to its output message; returns the number linked."""
        traces_to_update = []
        for trace_id, chat_id in batch:
            message = self._find_output_message(trace_id, chat_id)
            if message:
                traces_to_update.append(Trace(id=trace_id, output_message=message))

        if traces_to_update and not dry_run:
            Trace.objects.bulk_update(traces_to_update, ["output_message"], batch_size=self.batch_size)
        return len(traces_to_update)

    def _find_output_message(self, trace_id, chat_id) -> ChatMessage | None:
        """Find the AI message in the trace's chat that references the trace in its metadata.

        ``trace_info`` is a list of per-provider entries; the OCS entry stores the Trace pk
        as an integer, so JSONB containment will not match external providers' string ids.
        Legacy messages stored ``trace_info`` as a single dict. If multiple messages match,
        the latest one (pk as tie-breaker) is taken as the final bot response of the trace.
        """
        return (
            ChatMessage.objects.filter(chat_id=chat_id, message_type=ChatMessageType.AI)
            .filter(
                Q(metadata__trace_info__contains=[{"trace_id": trace_id}]) | Q(metadata__trace_info__trace_id=trace_id)
            )
            .order_by("created_at", "id")
            .last()
        )
