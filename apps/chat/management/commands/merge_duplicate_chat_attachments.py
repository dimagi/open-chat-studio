from django.db.models import Count

from apps.chat.models import ChatAttachment
from apps.data_migrations.management.commands.base import IdempotentCommand


class Command(IdempotentCommand):
    help = "Merge duplicate ChatAttachment records with the same (chat, tool_type) pair"
    migration_name = "merge_duplicate_chat_attachments_2026_02_12"

    def perform_migration(self, dry_run=False):
        duplicates = (
            ChatAttachment.objects.values("chat_id", "tool_type").annotate(count=Count("id")).filter(count__gt=1)
        )

        if not duplicates.exists():
            self.stdout.write(self.style.SUCCESS("No duplicate ChatAttachments found"))
            return 0

        total_deleted = 0
        for dup in duplicates:
            attachments = list(
                ChatAttachment.objects.filter(
                    chat_id=dup["chat_id"],
                    tool_type=dup["tool_type"],
                ).order_by("created_at")
            )
            keeper = attachments[0]
            to_merge = attachments[1:]

            if dry_run:
                self.stdout.write(
                    f"  Chat {dup['chat_id']}, tool_type={dup['tool_type']}: "
                    f"keep #{keeper.id}, merge {len(to_merge)} duplicate(s)"
                )
                total_deleted += len(to_merge)
                continue

            for other in to_merge:
                # Merge M2M files into keeper
                keeper.files.add(*other.files.all())

                # Merge extra JSON (keeper values take precedence)
                merged_extra = {**other.extra, **keeper.extra}
                if merged_extra != keeper.extra:
                    keeper.extra = merged_extra
                    keeper.save(update_fields=["extra"])

                other.delete()
                total_deleted += 1

        action = "Would delete" if dry_run else "Deleted"
        self.stdout.write(f"{action} {total_deleted} duplicate ChatAttachment(s)")
        return total_deleted
