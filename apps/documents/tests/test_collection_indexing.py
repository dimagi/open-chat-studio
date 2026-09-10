from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.db.models import QuerySet

from apps.documents.models import CollectionFile, FileStatus
from apps.documents.tasks import (
    index_collection_files,
    index_collection_files_task,
)
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_clears_the_failure_reason(add_files_to_index_mock, remote_collection_index):
    """Every re-index route funnels through this transition, so it is where a reason from a
    previous attempt stops applying. Indexing itself is patched out, so nothing downstream can
    clear it: an empty reason here proves the IN_PROGRESS transition did it."""
    collection_file = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.FAILED,
        failure_reason="ValueError: Error code: 401 - Incorrect API key provided",
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )

    index_collection_files_task([collection_file.id])

    collection_file.refresh_from_db()
    assert collection_file.status == FileStatus.IN_PROGRESS
    assert collection_file.failure_reason == ""


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_fails_the_group_when_indexing_raises(add_files_to_index_mock, remote_collection_index):
    """A failure that no handler inside add_files covers still has to leave the group in a
    terminal status, because IN_PROGRESS renders as a spinner for as long as the row exists."""
    add_files_to_index_mock.side_effect = DatabaseError("connection already closed")
    collection_file = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )

    with pytest.raises(DatabaseError):
        index_collection_files_task([collection_file.id])

    collection_file.refresh_from_db()
    assert collection_file.status == FileStatus.FAILED
    assert collection_file.failure_reason == "DatabaseError: connection already closed"


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_leaves_rows_outside_the_group_alone(add_files_to_index_mock, remote_collection_index):
    """Groups are indexed one chunking strategy at a time. A row not yet reached is still
    PENDING and has not failed at anything."""
    add_files_to_index_mock.side_effect = DatabaseError("connection already closed")
    failing_row = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    untouched_row = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 100}},
    )

    with pytest.raises(DatabaseError):
        index_collection_files_task([failing_row.id, untouched_row.id])

    failing_row.refresh_from_db()
    untouched_row.refresh_from_db()
    assert failing_row.status == FileStatus.FAILED
    assert untouched_row.status == FileStatus.PENDING
    assert untouched_row.failure_reason == ""


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_keeps_a_reason_already_written_by_add_files(
    add_files_to_index_mock, remote_collection_index
):
    """add_files writes a reason naming the stage that failed. That is more specific than
    whatever killed the surrounding call, so it is left in place."""
    collection_file = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )

    def fail_the_row_then_raise(*args, **kwargs):
        CollectionFile.objects.filter(pk=collection_file.pk).update(
            status=FileStatus.FAILED, failure_reason="FileUploadError: Incorrect API key provided: sk-abc"
        )
        raise DatabaseError("connection already closed")

    add_files_to_index_mock.side_effect = fail_the_row_then_raise

    with pytest.raises(DatabaseError):
        index_collection_files_task([collection_file.id])

    collection_file.refresh_from_db()
    assert collection_file.status == FileStatus.FAILED
    assert collection_file.failure_reason == "FileUploadError: Incorrect API key provided: sk-abc"


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_propagates_the_error_that_stopped_indexing(
    add_files_to_index_mock, remote_collection_index
):
    """A caller holding a transaction cannot accept the recovery write. The error that stopped
    indexing is the one worth reporting."""
    add_files_to_index_mock.side_effect = ValueError("the original failure")
    collection_file = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    original_update = QuerySet.update

    def fail_only_the_recovery_write(self, **kwargs):
        if kwargs.get("status") == FileStatus.FAILED:
            raise DatabaseError("current transaction is aborted")
        return original_update(self, **kwargs)

    with patch.object(QuerySet, "update", fail_only_the_recovery_write):
        with pytest.raises(ValueError, match="the original failure"):
            index_collection_files_task([collection_file.id])


class _RowsThatFailToClose:
    """Yields the rows of an already-fetched queryset, then raises from close(), mimicking a
    server-side cursor whose CLOSE fails against an already-aborted transaction."""

    def __init__(self, rows):
        self._rows = iter(rows)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._rows)

    def close(self):
        raise RuntimeError("cursor already closed")


class _QuerySetThatFailsToClose:
    """Proxies the calls `index_collection_files` makes on its `collection_files_queryset`
    argument, substituting `_RowsThatFailToClose` for the iterator it hands back.

    Deliberately minimal: it carries only the three queryset methods that function calls."""

    def __init__(self, queryset):
        self._queryset = queryset

    def first(self):
        return self._queryset.first()

    def select_related(self, *fields):
        return _QuerySetThatFailsToClose(self._queryset.select_related(*fields))

    def iterator(self, chunk_size):
        return _RowsThatFailToClose(list(self._queryset.iterator(chunk_size)))


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_index_collection_files_propagates_the_error_when_the_cursor_close_also_fails(
    add_files_to_index_mock, remote_collection_index
):
    """Closing an abandoned cursor can itself fail, on a connection whose transaction the
    original error already aborted. The original error is still the one worth reporting."""
    add_files_to_index_mock.side_effect = ValueError("the original failure")
    collection_file = CollectionFile.objects.create(
        file=FileFactory.create(team=remote_collection_index.team),
        collection=remote_collection_index,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    queryset = _QuerySetThatFailsToClose(CollectionFile.objects.filter(id=collection_file.id))

    with pytest.raises(ValueError, match="the original failure"):
        index_collection_files(collection_files_queryset=queryset)
