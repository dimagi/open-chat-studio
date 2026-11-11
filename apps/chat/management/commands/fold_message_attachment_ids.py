from django.db import connection
from django.db.models import Max, Min

from apps.chat.models import ChatMessage, ChatMessageMetadataKeys
from apps.data_migrations.management.commands.base import IdempotentCommand

BATCH_SIZE = 10_000
LEGACY_KEYS = [ChatMessageMetadataKeys.CITED_FILES.value, ChatMessageMetadataKeys.GENERATED_FILES.value]
TARGET_KEY = ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS.value

# updated_at is left alone: the file references are unchanged, and bumping it would re-send every
# AI message to incremental exports.
FOLD_SQL = f"""
    UPDATE {ChatMessage._meta.db_table} AS message
    SET metadata = (message.metadata - %(legacy)s::text[]) || folded.patch
    FROM (
        SELECT candidate.id,
            CASE WHEN count(file_id) = 0 THEN '{{}}'::jsonb
                ELSE jsonb_build_object(%(target)s, jsonb_agg(DISTINCT file_id)) END AS patch
        FROM {ChatMessage._meta.db_table} AS candidate
        LEFT JOIN LATERAL jsonb_array_elements(
            coalesce(candidate.metadata -> %(target)s, '[]')
            || coalesce(candidate.metadata -> %(cited)s, '[]')
            || coalesce(candidate.metadata -> %(generated)s, '[]')
        ) AS file_id ON true
        WHERE candidate.id >= %(start)s AND candidate.id < %(stop)s AND candidate.metadata ?| %(legacy)s::text[]
        GROUP BY candidate.id
    ) AS folded
    WHERE message.id = folded.id
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
