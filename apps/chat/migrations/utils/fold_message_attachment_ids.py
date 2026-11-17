"""Move the file ids chat messages hold under ``cited_files`` and ``generated_files`` into
``ocs_attachment_file_ids``. Migration ``chat.0033_fold_message_attachment_ids`` calls this with the
historical ``ChatMessage`` model.

The migration runs while the previous release serves requests, so each batch is one ``UPDATE`` that
computes the new value from the row it updates: an edit made while the batch waits on a row lock is
kept. ``updated_at`` is left alone, since the file references don't change and bumping it would
re-send every AI message to incremental exports.
"""

from django.db.models import Max, Min

BATCH_SIZE = 10_000
LEGACY_KEYS = ["cited_files", "generated_files"]
TARGET_KEY = "ocs_attachment_file_ids"

FOLD_SQL = """
    UPDATE {table}
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


def fold_message_attachment_ids(ChatMessage, connection) -> int:
    bounds = ChatMessage._base_manager.aggregate(first=Min("id"), last=Max("id"))
    if bounds["first"] is None:
        return 0

    sql = FOLD_SQL.format(table=connection.ops.quote_name(ChatMessage._meta.db_table))
    params = {"legacy": LEGACY_KEYS, "target": TARGET_KEY, "cited": LEGACY_KEYS[0], "generated": LEGACY_KEYS[1]}
    folded = 0
    with connection.cursor() as cursor:
        for start in range(bounds["first"], bounds["last"] + 1, BATCH_SIZE):
            cursor.execute(sql, {**params, "start": start, "stop": start + BATCH_SIZE})
            folded += cursor.rowcount
    return folded
