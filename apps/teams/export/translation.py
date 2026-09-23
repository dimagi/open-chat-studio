"""The FK translation table: every synced source row gets a (content_type, source_key) entry whose
target_key starts null and is filled once the row exists on the target. It is both the FK-remap map
and the checkpoint (a null target_key means "not created yet"), so a run can resume on rerun.

It lives in SQLite (a persistent, mounted path) while the synced rows live in the target's Postgres.
The same file holds each model's pagination cursor, namespaced by the chatbot selection the source
served it under."""

import base64
import hashlib
import json
import sqlite3
from collections.abc import Sequence

from django.utils.dateparse import parse_datetime

# The cursor namespace used when the source exports the whole team.
ALL_CHATBOTS_KEY = "all"


class FKTranslationStore:
    def __init__(self, path):
        """Open (creating if needed) the SQLite store at ``path`` and load its table into an
        in-memory index for fast lookups."""
        self._conn = sqlite3.connect(str(path))
        # A sync commits per row; WAL with synchronous=NORMAL avoids an fsync per commit. A crash can
        # lose the last few commits, which a rerun re-fetches.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS fk_translation ("
            "content_type TEXT NOT NULL, source_key INTEGER NOT NULL, target_key INTEGER, "
            "source_updated_at TEXT, "
            "PRIMARY KEY (content_type, source_key))"
        )
        self._conn.execute("CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cursors ("
            "selection_key TEXT NOT NULL, model_label TEXT NOT NULL, cursor TEXT, "
            "PRIMARY KEY (selection_key, model_label))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS selections (selection_key TEXT PRIMARY KEY, public_ids TEXT NOT NULL)"
        )
        self._add_missing_columns()
        self._conn.commit()
        self._index: dict[str, dict[int, int | None]] = {}
        self._source_timestamps: dict[str, dict[int, str | None]] = {}
        for content_type, source_key, target_key, source_updated_at in self._conn.execute(
            "SELECT content_type, source_key, target_key, source_updated_at FROM fk_translation"
        ):
            self._index.setdefault(content_type, {})[source_key] = target_key
            self._source_timestamps.setdefault(content_type, {})[source_key] = source_updated_at

    def _add_missing_columns(self) -> None:
        """Bring a store created by an earlier release up to the current schema. CREATE TABLE IF NOT
        EXISTS leaves an existing table alone, so a column added later has to be added by hand."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(fk_translation)")}
        if "source_updated_at" not in existing:
            self._conn.execute("ALTER TABLE fk_translation ADD COLUMN source_updated_at TEXT")

    def record(
        self,
        content_type: str,
        source_key: int,
        target_key: int | None = None,
        source_updated_at: str | None = None,
    ) -> None:
        """Upsert a source->target mapping. A null ``target_key`` is the checkpoint marker meaning
        "synced but not yet created on the target"; it's filled in once the row exists.
        ``source_updated_at`` is the source row's timestamp verbatim, used to skip a re-read of a row
        that hasn't changed."""
        self._conn.execute(
            "INSERT INTO fk_translation (content_type, source_key, target_key, source_updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (content_type, source_key) DO UPDATE SET "
            "target_key = excluded.target_key, source_updated_at = excluded.source_updated_at",
            (content_type, source_key, target_key, source_updated_at),
        )
        self._conn.commit()
        self._index.setdefault(content_type, {})[source_key] = target_key
        self._source_timestamps.setdefault(content_type, {})[source_key] = source_updated_at

    def get_source_updated_at(self, content_type: str, source_key: int) -> str | None:
        """The source ``updated_at`` recorded with the row, or None when it's unknown."""
        return self._source_timestamps.get(content_type, {}).get(source_key)

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

    def get_cursor(self, selection_key: str, model_label: str) -> str | None:
        """Where to resume this model's pull. Cursors are namespaced by selection: the source serves
        a different row set per chatbot selection, so a cursor from one selection would skip rows
        that a wider one puts below it."""
        row = self._conn.execute(
            "SELECT cursor FROM cursors WHERE selection_key = ? AND model_label = ?",
            (selection_key, model_label),
        ).fetchone()
        return row[0] if row else None

    def set_cursor(self, selection_key: str, model_label: str, cursor: str | None) -> None:
        self._conn.execute(
            "INSERT INTO cursors (selection_key, model_label, cursor) VALUES (?, ?, ?) "
            "ON CONFLICT (selection_key, model_label) DO UPDATE SET cursor = excluded.cursor",
            (selection_key, model_label, cursor),
        )
        self._conn.commit()

    def cursors_for(self, selection_key: str) -> dict[str, str | None]:
        return {
            model_label: cursor
            for model_label, cursor in self._conn.execute(
                "SELECT model_label, cursor FROM cursors WHERE selection_key = ?", (selection_key,)
            )
        }

    def record_selection(self, selection_key: str, public_ids: Sequence[str]) -> None:
        """Remember which chatbots a key stands for, so a later run can tell a narrower selection
        from a wider one."""
        self._conn.execute(
            "INSERT INTO selections (selection_key, public_ids) VALUES (?, ?) "
            "ON CONFLICT (selection_key) DO UPDATE SET public_ids = excluded.public_ids",
            (selection_key, json.dumps(sorted(public_ids))),
        )
        self._conn.commit()

    def selections(self) -> dict[str, list[str]]:
        rows = self._conn.execute("SELECT selection_key, public_ids FROM selections")
        return {key: json.loads(ids) for key, ids in rows}

    def seed_cursors_from(self, source_key: str, target_key: str) -> None:
        """Copy one selection's cursors to another, leaving any the target already has alone."""
        self._conn.execute(
            "INSERT INTO cursors (selection_key, model_label, cursor) "
            "SELECT ?, model_label, cursor FROM cursors WHERE selection_key = ? "
            "ON CONFLICT (selection_key, model_label) DO NOTHING",
            (target_key, source_key),
        )
        self._conn.commit()

    def has_unfilled_targets(self) -> bool:
        """True if any recorded row still lacks a target -- i.e. a prior run was interrupted."""
        return any(tgt is None for rows in self._index.values() for tgt in rows.values())

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FKTranslationStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


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


def selection_key(public_ids: Sequence[str]) -> str:
    """The cursor namespace for one chatbot selection. Order-independent, so the key only moves when
    the set itself does."""
    if not public_ids:
        return ALL_CHATBOTS_KEY
    return hashlib.sha256("\n".join(sorted(public_ids)).encode()).hexdigest()[:32]


def page_cursor(cursor_type: str, rows: list[dict]) -> str | None:
    """The resume cursor for the page just imported, derived from its own rows. The endpoint only
    returns a cursor while more rows remain, so the last page of a run carries none."""
    if not rows:
        return None
    if cursor_type == "pk":
        return derive_pk_cursor([row["id"] for row in rows])
    return derive_updated_at_cursor((parse_datetime(row["updated_at"]), row["id"]) for row in rows)
