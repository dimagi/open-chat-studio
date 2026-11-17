from django.db import connection
from django.db.models import Max, Min

from apps.chat.models import ChatMessage, ChatMessageMetadataKeys
from apps.data_migrations.management.commands.base import IdempotentCommand

BATCH_SIZE = 10_000
LEGACY_KEYS = [ChatMessageMetadataKeys.CITED_FILES.value, ChatMessageMetadataKeys.GENERATED_FILES.value]
TARGET_KEY = ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS.value

# The new value is computed from the updated row itself, so a row changed while this waits on its
# lock is re-read rather than overwritten. updated_at is left alone: the file references are
# unchanged, and bumping it would re-send every AI message to incremental exports.
FOLD_SQL = f"""
    UPDATE {ChatMessage._meta.db_table}
    SET metadata = (metadata - %(legacy)s::text[]) || coalesce((
        SELECT jsonb_build_object(%(target)s, jsonb_agg(DISTINCT file_id))
        FROM jsonb_array_elements(
            coalesce(metadata -> %(target)s, '[]')
            || coalesce(metadata -> %(cited)s, '[]')
            || coalesce(metadata -> %(generated)s, '[]')
        ) AS file_id
        HAVING count(*) > 0
    ), '{{}}'::jsonb)
    WHERE id >= %(start)s AND id < %(stop)s AND metadata ?| %(legacy)s::text[]
"""


class Command(IdempotentCommand):
    help = "Move file ids under cited_files and generated_files in chat message metadata into ocs_attachment_file_ids"
    migration_name = "fold_message_attachment_ids_2026_11_11"
    atomic = False
    disable_audit = True

    def perform_migration(self, dry_run=False):
        if dry_run:
            count = ChatMessage.objects.filter(metadata__has_any_keys=LEGACY_KEYS).count()
            self.stdout.write(f"Would fold {count} messages")
            return None

        bounds = ChatMessage.objects.aggregate(first=Min("id"), last=Max("id"))
        if bounds["first"] is None:
            return 0

        params = {"legacy": LEGACY_KEYS, "target": TARGET_KEY, "cited": LEGACY_KEYS[0], "generated": LEGACY_KEYS[1]}
        folded = 0
        with connection.cursor() as cursor:
            for start in range(bounds["first"], bounds["last"] + 1, BATCH_SIZE):
                cursor.execute(FOLD_SQL, {**params, "start": start, "stop": start + BATCH_SIZE})
                folded += cursor.rowcount
                checked = min(start + BATCH_SIZE - 1, bounds["last"])
                self.stdout.write(f"Folded {folded} messages, checked ids up to {checked} of {bounds['last']}")
        return folded
