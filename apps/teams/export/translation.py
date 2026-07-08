"""The FK translation table: every synced source row gets a (content_type, source_key) entry whose
target_key starts null and is filled once the row exists on the target. It is both the FK-remap map
and the checkpoint (a null target_key means "not created yet"), so a run can resume on rerun.

It lives in SQLite (a persistent, mounted path) while the synced rows live in the target's Postgres,
so per-slug cursors aren't stored separately -- they're derived from the rows already synced."""

import base64
import json
import sqlite3


class FKTranslationStore:
    def __init__(self, path):
        """Open (creating if needed) the SQLite store at ``path`` and load its table into an
        in-memory index for fast lookups."""
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS fk_translation ("
            "content_type TEXT NOT NULL, source_key INTEGER NOT NULL, target_key INTEGER, "
            "PRIMARY KEY (content_type, source_key))"
        )
        self._conn.execute("CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY)")
        self._conn.commit()
        self._index: dict[str, dict[int, int | None]] = {}
        for content_type, source_key, target_key in self._conn.execute(
            "SELECT content_type, source_key, target_key FROM fk_translation"
        ):
            self._index.setdefault(content_type, {})[source_key] = target_key

    def record(self, content_type: str, source_key: int, target_key: int | None = None) -> None:
        """Upsert a source->target mapping. A null ``target_key`` is the checkpoint marker meaning
        "synced but not yet created on the target"; it's filled in once the row exists."""
        self._conn.execute(
            "INSERT INTO fk_translation (content_type, source_key, target_key) VALUES (?, ?, ?) "
            "ON CONFLICT (content_type, source_key) DO UPDATE SET target_key = excluded.target_key",
            (content_type, source_key, target_key),
        )
        self._conn.commit()
        self._index.setdefault(content_type, {})[source_key] = target_key

    def get_target(self, content_type: str, source_key: int) -> int | None:
        """The target pk for a source row, or None if it's unrecorded or not yet created."""
        return self._index.get(content_type, {}).get(source_key)

    def has_target(self, content_type: str, source_key: int) -> bool:
        """True once the source row has been created on the target (non-null target_key)."""
        return self.get_target(content_type, source_key) is not None

    def committed_targets(self, content_type: str) -> dict[int, int]:
        """All source->target mappings for a model that have been created, dropping the checkpoints
        whose target isn't filled in yet."""
        return {src: tgt for src, tgt in self._index.get(content_type, {}).items() if tgt is not None}

    def max_source_key(self, content_type: str) -> int | None:
        """Highest source pk already created for a model -- the pk cursor for resuming the pull."""
        committed = self.committed_targets(content_type)
        return max(committed) if committed else None

    def has_flag(self, name: str) -> bool:
        """True if the flag was recorded by an earlier run (e.g. a confirmation already given); flags
        persist in the state DB until it is reset."""
        return self._conn.execute("SELECT 1 FROM flags WHERE name = ?", (name,)).fetchone() is not None

    def set_flag(self, name: str) -> None:
        self._conn.execute("INSERT OR IGNORE INTO flags (name) VALUES (?)", (name,))
        self._conn.commit()

    def has_unfilled_targets(self) -> bool:
        """True if any recorded row still lacks a target -- i.e. a prior run was interrupted."""
        return any(tgt is None for rows in self._index.values() for tgt in rows.values())


def derive_pk_cursor(source_keys) -> str | None:
    return str(max(source_keys)) if source_keys else None


def derive_updated_at_cursor(rows) -> str | None:
    """rows: an iterable of (updated_at, source_id). Returns the keyset cursor for the latest row."""
    rows = list(rows)
    if not rows:
        return None
    updated_at, source_id = max(rows, key=lambda r: (r[0], r[1]))
    keyset = {"updated_at": updated_at.isoformat(), "id": source_id}
    return base64.b64encode(json.dumps(keyset).encode()).decode()
