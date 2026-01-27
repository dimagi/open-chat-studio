from django.db import transaction
from django.db.models import OuterRef, Subquery

from apps.chat.models import ChatMessage, ChatMessageType
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession


class Command(IdempotentCommand):
    help = "Backfill first_activity_at on ExperimentSession based on the created_at timestamp of the first ChatMessage."
    migration_name = "backfill_experiment_first_activity_at_2026_01_27"
    atomic = False
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Select all the IDs from Sessions with human messages
        all_session_ids = list(
            ExperimentSession.objects.filter(
                first_activity_at__isnull=True, chat__messages__message_type=ChatMessageType.HUMAN
            )
            .distinct()
            .values_list("id", flat=True)
        )

        total = len(all_session_ids)
        batch_size = 500

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No sessions need processing"))
            return

        if dry_run:
            self.stdout.write(f"Would update {total} sessions")
            return

        self.stdout.write(f"Found {total} sessions that need processing")

        # Process collected sessions in a batch
        for i in range(0, total, batch_size):
            batch_ids = all_session_ids[i : i + batch_size]

            with transaction.atomic():
                first_human_message = (
                    ChatMessage.objects.filter(
                        chat_id=OuterRef("chat_id"),
                        message_type=ChatMessageType.HUMAN,
                    )
                    .order_by("created_at")
                    .values("created_at")[:1]
                )

                total_updated = ExperimentSession.objects.filter(id__in=batch_ids).update(
                    first_activity_at=Subquery(first_human_message)
                )

            processed = i + len(batch_ids)
            print(f"Processed {processed}/{total} (updated {total_updated} in this batch)")

        msg = f"Successfully updated {total_updated} sessions"
        self.stdout.write(self.style.SUCCESS(msg))
