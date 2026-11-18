import threading
import time
from importlib import import_module
from unittest.mock import Mock

import pytest
from django.apps import apps as global_apps
from django.db import connection, transaction

from apps.chat.migrations.utils import fold_message_attachment_ids as fold
from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory

ATTACHMENTS = "ocs_attachment_file_ids"

migration_0033 = import_module("apps.chat.migrations.0033_fold_message_attachment_ids")


def _message(chat, metadata):
    return ChatMessageFactory.create(chat=chat, message_type="ai", content="hi", metadata=metadata)


def _metadata(message):
    """The stored metadata with the attachment ids sorted, since the fold does not keep their order."""
    metadata = ChatMessage.objects.get(id=message.id).metadata
    if ATTACHMENTS in metadata:
        metadata[ATTACHMENTS] = sorted(metadata[ATTACHMENTS])
    return metadata


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            {ATTACHMENTS: [1], "cited_files": [1, 2], "generated_files": [3], "trace_info": {"trace_id": "t"}},
            {ATTACHMENTS: [1, 2, 3], "trace_info": {"trace_id": "t"}},
            id="folds-and-dedupes",
        ),
        pytest.param({"cited_files": [4], "generated_files": []}, {ATTACHMENTS: [4]}, id="no-existing-attachments"),
        pytest.param({"cited_files": [], "generated_files": []}, {}, id="empty-lists-are-dropped"),
        pytest.param({ATTACHMENTS: [5], "cited_files": []}, {ATTACHMENTS: [5]}, id="empty-legacy-keeps-attachments"),
        pytest.param({ATTACHMENTS: [6], "openai_file_ids": ["f"]}, None, id="untouched"),
    ],
)
def test_folds_legacy_keys_into_the_attachment_key(before, after):
    message = _message(ChatFactory.create(), before)

    fold.fold_message_attachment_ids(ChatMessage, connection)

    assert _metadata(message) == (before if after is None else after)


@pytest.mark.django_db()
def test_every_batch_is_folded(monkeypatch):
    monkeypatch.setattr(fold, "BATCH_SIZE", 2)
    chat = ChatFactory.create()
    messages = [_message(chat, {"cited_files": [i]}) for i in range(5)]

    fold.fold_message_attachment_ids(ChatMessage, connection)

    assert [_metadata(message) for message in messages] == [{ATTACHMENTS: [i]} for i in range(5)]


@pytest.mark.django_db()
def test_does_not_touch_updated_at():
    message = _message(ChatFactory.create(), {"generated_files": [1]})
    updated_at = ChatMessage.objects.get(id=message.id).updated_at

    fold.fold_message_attachment_ids(ChatMessage, connection)

    assert ChatMessage.objects.get(id=message.id).updated_at == updated_at


@pytest.mark.django_db(transaction=True)
def test_a_concurrent_edit_is_not_undone():
    """Moderation strips a reply's attachments while the backfill waits on its row lock."""
    message = _message(ChatFactory.create(), {"cited_files": [1]})
    locked, release = threading.Event(), threading.Event()

    def moderate():
        try:
            with transaction.atomic():
                reply = ChatMessage.objects.select_for_update().get(id=message.id)
                reply.metadata = {}
                reply.save(update_fields=["metadata"])
                locked.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    def backfill():
        try:
            fold.fold_message_attachment_ids(ChatMessage, connection)
        finally:
            connection.close()

    moderation, backfill_run = threading.Thread(target=moderate), threading.Thread(target=backfill)
    moderation.start()
    locked.wait(timeout=10)
    backfill_run.start()
    _wait_for_a_blocked_query()
    release.set()
    moderation.join()
    backfill_run.join()

    assert _metadata(message) == {}


def _wait_for_a_blocked_query():
    with connection.cursor() as cursor:
        for _ in range(100):
            cursor.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
            if cursor.fetchone():
                return
            time.sleep(0.05)
    raise AssertionError("the backfill never waited on the row lock")


@pytest.mark.django_db()
def test_the_migration_folds_messages():
    message = _message(ChatFactory.create(), {"cited_files": [1]})

    migration_0033.fold_message_attachment_ids(global_apps, Mock(connection=connection))

    assert _metadata(message) == {ATTACHMENTS: [1]}
