from collections import defaultdict

from django.db.models import F, Q
from django.utils import timezone

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.files.models import File, FilePurpose

# Expiry to backfill onto export files that never had one. Matches the value
# set on newly created exports in experiments/evaluations tasks.
EXPORT_EXPIRY = timezone.timedelta(days=7)

# Conditions that reliably identify a file's purpose, in precedence order. A
# file is assigned to the first rule it matches; anything that matches nothing
# (e.g. inbound media attached as "ocs_attachments", which is indistinguishable
# from a user upload after the fact) is left untouched.
RULES: list[tuple[str, Q]] = [
    (FilePurpose.COLLECTION, Q(collections__isnull=False) | Q(document_sources__isnull=False)),
    (FilePurpose.ASSISTANT, Q(toolresources__isnull=False)),
    (FilePurpose.MESSAGE_MEDIA, Q(chatattachment__tool_type="voice_message")),
    (FilePurpose.ASSISTANT, Q(chatattachment__tool_type__in=["code_interpreter", "file_search"])),
    (FilePurpose.ASSISTANT, Q(external_source="openai")),
    (FilePurpose.DATA_EXPORT, Q(content_type="application/gzip", name__icontains="Chat Export")),
    (FilePurpose.DATA_EXPORT, Q(content_type="text/csv", name__icontains="_latest_results_")),
    # Generated collection exports are the only ZIPs we create with a purpose;
    # exclude ZIPs attached to a chat so user uploads aren't classified (and expired).
    (FilePurpose.DATA_EXPORT, Q(content_type="application/zip") & Q(chatattachment__isnull=True)),
]

UPDATE_BATCH_SIZE = 5000


class Command(IdempotentCommand):
    help = "Backfill File.purpose (and expiry_date for exports) on rows created before purpose was set consistently"
    migration_name = "backfill_file_purpose_2026_06_30"

    def perform_migration(self, dry_run=False):
        # Resolve each unset file to a single purpose, respecting rule precedence.
        assigned: dict[int, str] = {}
        for purpose, condition in RULES:
            for file_id in File.objects.filter(condition, purpose="").values_list("pk", flat=True):
                assigned.setdefault(file_id, purpose)

        by_purpose: dict[str, list[int]] = defaultdict(list)
        for file_id, purpose in assigned.items():
            by_purpose[purpose].append(file_id)

        for purpose in FilePurpose.values:
            ids = by_purpose.get(purpose, [])
            if not ids:
                continue
            self.stdout.write(f"  {purpose}: {len(ids)}")
            if not dry_run:
                for start in range(0, len(ids), UPDATE_BATCH_SIZE):
                    File.objects.filter(pk__in=ids[start : start + UPDATE_BATCH_SIZE]).update(purpose=purpose)

        # Export files with no expiry get one relative to creation so the existing
        # clean_up_expired_files sweep can remove stale exports from storage.
        expiry_condition = Q(expiry_date__isnull=True) & (
            Q(purpose=FilePurpose.DATA_EXPORT) | Q(pk__in=by_purpose.get(FilePurpose.DATA_EXPORT, []))
        )
        expiry_count = File.objects.filter(expiry_condition).count()
        self.stdout.write(f"  expiry_date backfilled: {expiry_count}")
        if not dry_run and expiry_count:
            File.objects.filter(expiry_condition).update(expiry_date=F("created_at") + EXPORT_EXPIRY)

        unclassified = File.objects.filter(purpose="").exclude(pk__in=assigned.keys()).count()
        self.stdout.write(f"  unchanged (ambiguous/unknown): {unclassified}")

        return {"purpose_set": len(assigned), "expiry_set": expiry_count}
