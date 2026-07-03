from collections import defaultdict

from django.db.models import F, Q
from django.utils import timezone

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.files.models import File, FilePurpose

# Expiry to backfill onto export files that never had one. Matches the value
# set on newly created exports in experiments/evaluations tasks.
EXPORT_EXPIRY = timezone.timedelta(days=7)

# Conditions that reliably identify a file's purpose, in precedence order. A
# file is assigned to the first rule it matches; anything that matches nothing is
# left untouched. ASSISTANT is reserved for bot-configuration files (assistant
# tool resources and openai-synced files); any file attached to a conversation
# (ChatAttachment, any tool_type) is MESSAGE_MEDIA. The conversation rule sits
# above the export patterns so a user-uploaded ZIP attached to a chat is treated
# as media rather than a (short-lived) export.
RULES: list[tuple[str, Q]] = [
    (FilePurpose.COLLECTION, Q(collections__isnull=False) | Q(document_sources__isnull=False)),
    (FilePurpose.ASSISTANT, Q(toolresources__isnull=False)),
    (FilePurpose.MESSAGE_MEDIA, Q(chatattachment__isnull=False)),
    (FilePurpose.ASSISTANT, Q(external_source="openai")),
    (FilePurpose.DATA_EXPORT, Q(content_type="application/gzip", name__icontains="Chat Export")),
    (FilePurpose.DATA_EXPORT, Q(content_type="text/csv", name__icontains="_latest_results_")),
    # Collection exports are named "<slug>_files_<timestamp>.zip"; match that pattern
    # so an arbitrary user-uploaded ZIP isn't classified as an export (and expired).
    (FilePurpose.DATA_EXPORT, Q(content_type="application/zip", name__icontains="_files_")),
]

UPDATE_BATCH_SIZE = 5000


class Command(IdempotentCommand):
    help = "Backfill File.purpose (and expiry_date for exports) on rows created before purpose was set consistently"
    migration_name = "backfill_file_purpose_2026_06_30"

    def perform_migration(self, dry_run=False):
        assigned = self._resolve_purposes()
        by_purpose: dict[str, list[int]] = defaultdict(list)
        for file_id, purpose in assigned.items():
            by_purpose[purpose].append(file_id)

        for purpose in FilePurpose.values:
            ids = by_purpose.get(purpose, [])
            if ids:
                self.stdout.write(f"  {purpose}: {len(ids)}")
            if ids and not dry_run:
                self._set_purpose(purpose, ids)

        expiry_count = self._backfill_export_expiry(by_purpose.get(FilePurpose.DATA_EXPORT, []), dry_run)
        self.stdout.write(f"  expiry_date backfilled: {expiry_count}")

        unclassified = File.objects.filter(purpose="").exclude(pk__in=assigned.keys()).count()
        self.stdout.write(f"  unchanged (ambiguous/unknown): {unclassified}")

        return {"purpose_set": len(assigned), "expiry_set": expiry_count}

    @staticmethod
    def _resolve_purposes() -> dict[int, str]:
        """Map each unset file to a single purpose, respecting RULES precedence."""
        assigned: dict[int, str] = {}
        for purpose, condition in RULES:
            for file_id in File.objects.filter(condition, purpose="").values_list("pk", flat=True):
                assigned.setdefault(file_id, purpose)
        return assigned

    @staticmethod
    def _set_purpose(purpose: str, ids: list[int]) -> None:
        for start in range(0, len(ids), UPDATE_BATCH_SIZE):
            File.objects.filter(pk__in=ids[start : start + UPDATE_BATCH_SIZE]).update(purpose=purpose)

    @staticmethod
    def _backfill_export_expiry(data_export_ids: list[int], dry_run: bool) -> int:
        """Give exports with no expiry one relative to creation, so the existing
        clean_up_expired_files sweep can remove stale exports from storage."""
        condition = Q(expiry_date__isnull=True) & (Q(purpose=FilePurpose.DATA_EXPORT) | Q(pk__in=data_export_ids))
        count = File.objects.filter(condition).count()
        if not dry_run and count:
            File.objects.filter(condition).update(expiry_date=F("created_at") + EXPORT_EXPIRY)
        return count
