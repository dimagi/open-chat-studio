import base64
import json
from datetime import UTC, datetime

import pytest

from apps.teams.export.translation import (
    FKTranslationStore,
    derive_pk_cursor,
    derive_updated_at_cursor,
)


def test_record_and_get_target_round_trip(tmp_path):
    store = FKTranslationStore(tmp_path / "team.sqlite")
    store.record("teams.team", 5, 99)
    assert store.get_target("teams.team", 5) == 99
    assert store.get_target("teams.team", 6) is None


def test_index_persists_across_reopen(tmp_path):
    path = tmp_path / "team.sqlite"
    FKTranslationStore(path).record("teams.team", 5, 99)
    assert FKTranslationStore(path).get_target("teams.team", 5) == 99


def test_null_target_is_a_checkpoint_until_filled(tmp_path):
    store = FKTranslationStore(tmp_path / "team.sqlite")
    store.record("chat.chat", 7)  # created marker, target not written yet
    assert store.has_target("chat.chat", 7) is False
    store.record("chat.chat", 7, 42)
    assert store.has_target("chat.chat", 7) is True
    assert store.get_target("chat.chat", 7) == 42


def test_max_source_key_ignores_uncommitted_rows(tmp_path):
    store = FKTranslationStore(tmp_path / "team.sqlite")
    store.record("chat.chat", 1, 10)
    store.record("chat.chat", 3, 30)
    store.record("chat.chat", 9)  # uncommitted: must not advance the cursor
    assert store.max_source_key("chat.chat") == 3


def test_has_unfilled_targets(tmp_path):
    store = FKTranslationStore(tmp_path / "team.sqlite")
    store.record("chat.chat", 1, 10)
    assert store.has_unfilled_targets() is False
    store.record("chat.chat", 2)
    assert store.has_unfilled_targets() is True


def test_committed_targets_excludes_uncommitted(tmp_path):
    store = FKTranslationStore(tmp_path / "team.sqlite")
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
