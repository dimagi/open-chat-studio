import base64
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from apps.teams.export.translation import (
    ALL_CHATBOTS_KEY,
    derive_pk_cursor,
    derive_updated_at_cursor,
    page_cursor,
)


def test_record_and_get_target_round_trip(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("teams.team", 5, 99)
    assert store.get_target("teams.team", 5) == 99
    assert store.get_target("teams.team", 6) is None


def test_index_persists_across_reopen(make_store, tmp_path):
    path = tmp_path / "team.sqlite"
    make_store(path).record("teams.team", 5, 99)
    assert make_store(path).get_target("teams.team", 5) == 99


def test_null_target_is_a_checkpoint_until_filled(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chat", 7)  # created marker, target not written yet
    assert store.has_target("chat.chat", 7) is False
    store.record("chat.chat", 7, 42)
    assert store.has_target("chat.chat", 7) is True
    assert store.get_target("chat.chat", 7) == 42


def test_max_source_key_ignores_uncommitted_rows(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chat", 1, 10)
    store.record("chat.chat", 3, 30)
    store.record("chat.chat", 9)  # uncommitted: must not advance the cursor
    assert store.max_source_key("chat.chat") == 3


def test_has_unfilled_targets(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chat", 1, 10)
    assert store.has_unfilled_targets() is False
    store.record("chat.chat", 2)
    assert store.has_unfilled_targets() is True


def test_committed_targets_excludes_uncommitted(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chat", 1, 10)
    store.record("chat.chat", 2)  # uncommitted
    assert store.committed_targets("chat.chat") == {1: 10}
    assert store.committed_targets("missing") == {}


@pytest.mark.parametrize(
    ("source_keys", "expected"),
    [
        pytest.param([3, 1, 2], "3", id="returns-max-key"),
        pytest.param([], None, id="empty-returns-none"),
    ],
)
def test_derive_pk_cursor_is_the_max_committed_key(source_keys, expected):
    assert derive_pk_cursor(source_keys) == expected


def test_derive_updated_at_cursor_picks_highest_keyset():
    early = datetime(2024, 1, 1, tzinfo=UTC)
    late = datetime(2024, 6, 1, tzinfo=UTC)
    cursor = derive_updated_at_cursor([(early, 50), (late, 2), (late, 8)])
    decoded = json.loads(base64.b64decode(cursor))
    assert decoded["updated_at"] == late.isoformat()
    assert decoded["id"] == 8  # highest id among the latest timestamp


def test_derive_updated_at_cursor_empty_is_none():
    assert derive_updated_at_cursor([]) is None


def test_record_round_trips_the_source_timestamp(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chatmessage", 5, 99, source_updated_at="2026-01-02T03:04:05+00:00")
    assert store.get_source_updated_at("chat.chatmessage", 5) == "2026-01-02T03:04:05+00:00"


def test_source_timestamp_persists_across_reopen(make_store, tmp_path):
    path = tmp_path / "team.sqlite"
    make_store(path).record("chat.chatmessage", 5, 99, source_updated_at="2026-01-02T03:04:05+00:00")
    assert make_store(path).get_source_updated_at("chat.chatmessage", 5) == "2026-01-02T03:04:05+00:00"


def test_source_timestamp_is_absent_for_an_unrecorded_row(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chatmessage", 5, 99)
    assert store.get_source_updated_at("chat.chatmessage", 5) is None
    assert store.get_source_updated_at("chat.chatmessage", 6) is None


def test_store_opens_a_database_written_before_the_timestamp_column(make_store, tmp_path):
    """State DBs created by an earlier release have no source_updated_at column; opening one must
    add it rather than fail, and the rows already in it read back as unknown."""
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE fk_translation (content_type TEXT NOT NULL, source_key INTEGER NOT NULL, "
        "target_key INTEGER, PRIMARY KEY (content_type, source_key))"
    )
    connection.execute("INSERT INTO fk_translation VALUES ('teams.team', 1, 7)")
    connection.commit()
    connection.close()

    store = make_store(path)
    assert store.get_target("teams.team", 1) == 7
    assert store.get_source_updated_at("teams.team", 1) is None


def test_store_uses_wal_journaling(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_cursor_round_trips(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") is None
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"


def test_cursors_are_kept_per_selection(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    store.set_cursor("deadbeef", "chat.chatmessage", "xyz")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"
    assert store.get_cursor("deadbeef", "chat.chatmessage") == "xyz"


def test_cursors_persist_across_reopen(make_store, tmp_path):
    path = tmp_path / "team.sqlite"
    make_store(path).set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    assert make_store(path).get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"


def test_cursors_for_returns_one_selection(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chat", "one")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "two")
    store.set_cursor("deadbeef", "chat.chat", "three")
    assert store.cursors_for(ALL_CHATBOTS_KEY) == {"chat.chat": "one", "chat.chatmessage": "two"}


def test_page_cursor_for_a_pk_resource():
    assert page_cursor("pk", [{"id": 3}, {"id": 7}]) == "7"
    assert page_cursor("pk", []) is None


def test_page_cursor_for_an_updated_at_resource():
    rows = [
        {"id": 3, "updated_at": "2026-01-01T00:00:00+00:00"},
        {"id": 7, "updated_at": "2026-01-02T00:00:00+00:00"},
    ]
    keyset = json.loads(base64.b64decode(page_cursor("updated_at_id", rows)))
    assert keyset == {"updated_at": "2026-01-02T00:00:00+00:00", "id": 7}
    assert page_cursor("updated_at_id", []) is None
