import logging
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from unittest.mock import ANY, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.documents.document_source_service import SyncResult
from apps.documents.exceptions import DocumentSourceDeleted, ZipCreationError, ZipIntegrityError
from apps.documents.models import SYNC_LOCK_TIMEOUT, CollectionFile, DocumentSource, FileStatus
from apps.documents.tasks import (
    async_create_collection_version,
    create_collection_zip_task,
    delete_collection_task,
    index_collection_files_task,
    migrate_vector_stores,
    sync_all_document_sources_task,
    sync_document_source_task,
)
from apps.files.models import File, FilePurpose
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.fixture()
def collection(db):
    llm_provider = LlmProviderFactory.create(name="test-provider")
    return CollectionFactory.create(
        name="test-collection",
        llm_provider=llm_provider,
        openai_vector_store_id="vs_123",
        is_index=True,
        is_remote_index=True,
    )


@pytest.mark.django_db()
def test_delete_collection_task_deletes_files_of_archived_collection():
    """delete_collection_task runs after Collection.archive() has already set is_archived=True, so it
    must load the archived row and delete its files. Regression: fetching via Collection.objects (which
    hides archived rows) made the task a silent no-op, leaving CollectionFile rows orphaned."""
    collection = CollectionFactory.create(is_index=False)
    file = FileFactory.create(team=collection.team)
    collection.files.add(file)
    collection.is_archived = True
    collection.save(update_fields=["is_archived"])
    assert CollectionFile.objects.filter(collection=collection).exists()

    with patch("apps.documents.utils.get_related_m2m_objects", return_value=[]):
        delete_collection_task(collection.id)

    assert not CollectionFile.objects.filter(collection=collection).exists()


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_collection_files_grouped_by_chunking_strategy(add_files_to_index_mock, collection):
    """Test that collection files are grouped by chunking strategy"""
    col_file_1 = CollectionFile.objects.create(
        file=FileFactory.create(team=collection.team),
        collection=collection,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    col_file_2 = CollectionFile.objects.create(
        file=FileFactory.create(team=collection.team),
        collection=collection,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 100}},
    )
    index_collection_files_task([col_file_1.id, col_file_2.id])

    # We expect two calls
    assert add_files_to_index_mock.call_count == 2

    # The first call should be for the first file with chunking strategy 800/400
    iterator_param = add_files_to_index_mock.mock_calls[0].kwargs["collection_files"]
    collection_file = list(iterator_param)[0]
    assert collection_file.id == col_file_1.id
    add_files_to_index_mock.assert_any_call(collection_files=ANY, chunk_size=800, chunk_overlap=400)

    # The second call should be for the second file with chunking strategy 1000/100
    iterator_param = add_files_to_index_mock.mock_calls[1].kwargs["collection_files"]
    collection_file = list(iterator_param)[0]
    assert collection_file.id == col_file_2.id
    add_files_to_index_mock.assert_any_call(collection_files=ANY, chunk_size=1000, chunk_overlap=100)


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_migrate_vector_stores_does_cleanup(add_files_to_index_mock, collection, remote_index_manager_mock):
    """Test that the migration task cleans up old vector stores"""
    previous_llm_provider = LlmProviderFactory.create(name="old-provider")

    file = FileFactory.create(team=collection.team, external_id="old-file-id")
    col_file = CollectionFile.objects.create(
        file=file,
        collection=collection,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    migrate_vector_stores(
        collection.id, from_vector_store_id="old_vs_123", from_llm_provider_id=previous_llm_provider.id
    )
    assert add_files_to_index_mock.call_count == 1
    iterator_param = add_files_to_index_mock.mock_calls[0].kwargs["collection_files"]
    collection_file = list(iterator_param)[0]
    assert collection_file.id == col_file.id
    add_files_to_index_mock.assert_any_call(collection_files=ANY, chunk_size=800, chunk_overlap=400)

    remote_index_manager_mock.delete_remote_index.assert_called()
    remote_index_manager_mock.client.files.delete.assert_called_once_with(file.external_id)


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_creates_zip_with_all_files(progress_recorder_mock):
    collection = CollectionFactory.create(name="test-collection")
    team = collection.team

    # Create manually uploaded files (no document_source)
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    file2 = FileFactory.create(team=team, name="file2.pdf", file=ContentFile(b"Content of file 2", name="file2.pdf"))
    file3 = FileFactory.create(team=team, name="file3.docx", file=ContentFile(b"Content of file 3", name="file3.docx"))

    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file2, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file3, collection=collection, document_source=None)

    # Create a file with a document source (should be excluded)
    document_source = DocumentSourceFactory.create(collection=collection, team=team)
    file4 = FileFactory.create(team=team, name="file4.txt", file=ContentFile(b"Content of file 4", name="file4.txt"))
    CollectionFile.objects.create(file=file4, collection=collection, document_source=document_source)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)
    assert zip_file_obj.team_id == team.id
    assert zip_file_obj.content_type == "application/zip"
    assert "test-collection" in zip_file_obj.name
    assert zip_file_obj.name.endswith(".zip")

    with zip_file_obj.file.open("rb") as f:
        zip_data = BytesIO(f.read())
        with zipfile.ZipFile(zip_data, "r") as zip_file:
            namelist = zip_file.namelist()
            assert len(namelist) == 3
            assert "file1.txt" in namelist
            assert "file2.pdf" in namelist
            assert "file3.docx" in namelist
            assert "file4.txt" not in namelist  # Should be excluded


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
@patch("apps.documents.tasks.logger")
def test_create_collection_zip_task_no_files_returns_none(logger_mock, progress_recorder_mock):
    collection = CollectionFactory.create(name="empty-collection")
    team = collection.team

    # Create only files with document sources (should be excluded)
    document_source = DocumentSourceFactory.create(collection=collection, team=team)
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=document_source)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is None
    logger_mock.warning.assert_called_once()
    assert f"No manually uploaded files found in collection {collection.id}" in str(logger_mock.warning.call_args)


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_handles_duplicate_filenames(progress_recorder_mock):
    """handles duplicate filenames by appending a numerical suffix."""
    collection = CollectionFactory.create(name="duplicate-collection")
    team = collection.team

    # Create files with duplicate names
    file1 = FileFactory.create(team=team, name="document.txt", file=ContentFile(b"First document", name="document.txt"))
    file2 = FileFactory.create(
        team=team, name="document.txt", file=ContentFile(b"Second document", name="document.txt")
    )
    file3 = FileFactory.create(team=team, name="document.txt", file=ContentFile(b"Third document", name="document.txt"))

    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file2, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file3, collection=collection, document_source=None)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)

    # Verify zip contents with renamed duplicates
    with zip_file_obj.file.open("rb") as f:
        zip_data = BytesIO(f.read())
        with zipfile.ZipFile(zip_data, "r") as zip_file:
            namelist = zip_file.namelist()
            assert len(namelist) == 3
            assert "document.txt" in namelist
            assert "document_1.txt" in namelist
            assert "document_2.txt" in namelist


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_no_collision_between_duplicate_and_real_file(progress_recorder_mock):
    """A file genuinely named document_1.txt must not collide with a renamed duplicate of document.txt."""
    collection = CollectionFactory.create(name="collision-collection")
    team = collection.team

    file1 = FileFactory.create(team=team, name="document.txt", file=ContentFile(b"original", name="document.txt"))
    file2 = FileFactory.create(team=team, name="document.txt", file=ContentFile(b"duplicate", name="document.txt"))
    file3 = FileFactory.create(team=team, name="document_1.txt", file=ContentFile(b"real one", name="document_1.txt"))

    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file2, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file3, collection=collection, document_source=None)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)
    with zip_file_obj.file.open("rb") as f:
        with zipfile.ZipFile(BytesIO(f.read()), "r") as zf:
            namelist = zf.namelist()
            assert len(namelist) == 3
            assert len(set(namelist)) == 3  # all names are unique


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
@patch("apps.documents.tasks.timezone")
def test_create_collection_zip_task_sets_expiry_date(timezone_mock, progress_recorder_mock):
    collection = CollectionFactory.create(name="expiry-test-collection")
    team = collection.team

    # Mock timezone to get predictable expiry date
    mock_now = timezone.now()
    timezone_mock.now.return_value = mock_now

    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)
    expected_expiry = mock_now + timedelta(hours=24)
    assert zip_file_obj.expiry_date == expected_expiry


# ---------------------------------------------------------------------------
# Failure-path tests
# ---------------------------------------------------------------------------


def _make_open_mock(exc: Exception):
    """Return a mock suitable for patching file.file.open that raises exc."""

    @contextmanager
    def _open(*args, **kwargs):
        raise exc
        yield  # noqa: unreachable — satisfies context manager protocol

    return _open


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "404", "Message": "NoSuchKey"}}, "GetObject"),
        EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
        FileNotFoundError("not found"),
        OSError("disk error"),
    ],
    ids=["s3_client_error", "endpoint_connection_error", "file_not_found", "os_error"],
)
def test_create_collection_zip_task_read_error_raises_and_creates_no_file(progress_recorder_mock, error):
    """Any file read error aborts the task and creates no File object (fail-fast)."""
    collection = CollectionFactory.create(name="read-error-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    with patch("django.db.models.fields.files.FieldFile.open", _make_open_mock(error)):
        with pytest.raises(ZipCreationError):
            create_collection_zip_task(collection.id, team.id)

    assert File.objects.filter(purpose=FilePurpose.DATA_EXPORT).count() == 0


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_second_file_error_aborts_and_creates_no_file(progress_recorder_mock):
    """A read error on the second file aborts the task — no partial ZIP is saved (fail-fast)."""
    collection = CollectionFactory.create(name="second-file-error-collection")
    team = collection.team

    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"Good content", name="file1.txt"))
    file2 = FileFactory.create(team=team, name="file2.txt", file=ContentFile(b"Bad content", name="file2.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file2, collection=collection, document_source=None)

    open_call_count = 0

    @contextmanager
    def open_first_ok_second_fails(*args, **kwargs):
        nonlocal open_call_count
        open_call_count += 1
        if open_call_count == 1:
            yield BytesIO(b"Good content")
        else:
            raise OSError("storage failure on second file")

    with patch("django.db.models.fields.files.FieldFile.open", open_first_ok_second_fails):
        with pytest.raises(ZipCreationError):
            create_collection_zip_task(collection.id, team.id)

    assert File.objects.filter(purpose=FilePurpose.DATA_EXPORT).count() == 0


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_empty_read_raises_zip_creation_error(progress_recorder_mock):
    """A silent 0-byte read detected by size check raises ZipCreationError."""
    collection = CollectionFactory.create(name="empty-read-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    File.objects.filter(id=file1.id).update(content_size=1024)
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    @contextmanager
    def open_returns_empty(*args, **kwargs):
        yield BytesIO(b"")

    with patch("django.db.models.fields.files.FieldFile.open", open_returns_empty):
        with pytest.raises(ZipCreationError, match="returned 0 bytes but expected 1024"):
            create_collection_zip_task(collection.id, team.id)

    assert File.objects.filter(purpose=FilePurpose.DATA_EXPORT).count() == 0


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_no_content_size_skips_size_check(progress_recorder_mock):
    """When content_size is None the size check is skipped — ZIP is created with 0-byte entry."""
    collection = CollectionFactory.create(name="no-content-size-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"", name="file1.txt"))
    File.objects.filter(id=file1.id).update(content_size=None)
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)
    with zip_file_obj.file.open("rb") as f:
        zip_data = BytesIO(f.read())
        with zipfile.ZipFile(zip_data, "r") as zf:
            assert "file1.txt" in zf.namelist()
            assert zf.read("file1.txt") == b""


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
@patch("apps.documents.tasks.logger")
def test_create_collection_zip_task_logs_warning_on_first_attempt(logger_mock, progress_recorder_mock):
    """On the first transient attempt (retries=0) the error is logged at WARNING level."""
    collection = CollectionFactory.create(name="log-warning-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    error = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "GetObject")
    with patch("django.db.models.fields.files.FieldFile.open", _make_open_mock(error)):
        with pytest.raises(ZipCreationError):
            create_collection_zip_task(collection.id, team.id)

    logger_mock.log.assert_called_once()
    log_level_used = logger_mock.log.call_args[0][0]
    assert log_level_used == logging.WARNING
    extra_used = logger_mock.log.call_args[1]["extra"]
    assert extra_used["retry"] == 0


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
@patch("apps.documents.tasks.logger")
def test_create_collection_zip_task_logs_error_on_final_retry(logger_mock, progress_recorder_mock):
    """On the final transient retry (retries == max_retries) the error is logged at ERROR level."""
    collection = CollectionFactory.create(name="log-error-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    max_retries = 3
    error = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "GetObject")

    create_collection_zip_task.push_request(retries=max_retries)
    try:
        with patch("django.db.models.fields.files.FieldFile.open", _make_open_mock(error)):
            with patch.object(create_collection_zip_task, "max_retries", max_retries):
                with pytest.raises(ZipCreationError):
                    create_collection_zip_task(collection.id, team.id)
    finally:
        create_collection_zip_task.pop_request()

    logger_mock.log.assert_called_once()
    log_level_used = logger_mock.log.call_args[0][0]
    assert log_level_used == logging.ERROR
    extra_used = logger_mock.log.call_args[1]["extra"]
    assert extra_used["retry"] == max_retries


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_celery_retries_on_zip_creation_error(progress_recorder_mock):
    """Celery's autoretry_for triggers self.retry when a transient ZipCreationError is raised."""
    collection = CollectionFactory.create(name="retry-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    retry_mock = MagicMock(side_effect=ZipCreationError("retry called"))
    error = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "GetObject")

    with patch("django.db.models.fields.files.FieldFile.open", _make_open_mock(error)):
        with patch.object(create_collection_zip_task, "retry", retry_mock):
            with pytest.raises(ZipCreationError):
                create_collection_zip_task(collection.id, team.id)

    retry_mock.assert_called_once()


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source_task")
def test_auto_sync_excludes_versioned_collections(mock_sync_task):
    """ADR-0031: snapshots and legacy frozen copies must never auto-sync."""
    working_index = CollectionFactory.create(is_index=True)
    working_source = DocumentSourceFactory.create(
        collection=working_index, source_type="github", auto_sync_enabled=True
    )

    snapshot_index = CollectionFactory.create(
        is_index=True,
        working_version=working_index,
        team=working_index.team,
        # Reuse providers: EmbeddingProviderModel has unique(team, name, type), so a second
        # factory-default instance on the same team would violate that constraint.
        llm_provider=working_index.llm_provider,
        embedding_provider_model=working_index.embedding_provider_model,
    )
    DocumentSourceFactory.create(collection=snapshot_index, source_type="github", auto_sync_enabled=True)

    sync_all_document_sources_task()

    dispatched_ids = set(mock_sync_task.map.call_args.args[0])
    assert dispatched_ids == {working_source.id}


# ---------------------------------------------------------------------------
# sync_document_source_task concurrency lock
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_sets_and_clears_lock(sync_mock):
    """On a normal run the task claims the lock under its own id, then releases it."""
    sync_mock.return_value = SyncResult(success=True)
    source = DocumentSourceFactory.create()

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    sync_mock.assert_called_once()
    source.refresh_from_db()
    assert source.sync_task_id == ""
    assert source.sync_started_at is None


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_skips_when_locked_by_another_running_task(sync_mock):
    """A fresh lock held by a different task short-circuits the sync without clobbering it.

    Regression: concurrent syncs of the same source raised
    ``DatabaseError: Save with update_fields did not affect any rows`` when one task deleted
    CollectionFile rows the other was updating.
    """
    source = DocumentSourceFactory.create(sync_task_id="other-task", sync_started_at=timezone.now())

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    sync_mock.assert_not_called()
    source.refresh_from_db()
    assert source.sync_task_id == "other-task"


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_reclaims_stale_lock(sync_mock):
    """A lock older than SYNC_LOCK_TIMEOUT (dead worker) is reclaimed rather than blocking forever."""
    sync_mock.return_value = SyncResult(success=True)
    stale = timezone.now() - SYNC_LOCK_TIMEOUT - timedelta(minutes=1)
    source = DocumentSourceFactory.create(sync_task_id="dead-task", sync_started_at=stale)

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    sync_mock.assert_called_once()
    source.refresh_from_db()
    assert source.sync_task_id == ""
    assert source.sync_started_at is None


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_releases_lock_on_exception(sync_mock):
    """The lock is released in a finally, even when the sync raises, so it never sticks."""
    sync_mock.side_effect = RuntimeError("boom")
    source = DocumentSourceFactory.create()

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    source.refresh_from_db()
    assert source.sync_task_id == ""
    assert source.sync_started_at is None


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_only_clears_lock_it_still_owns(sync_mock):
    """Completion clears the lock only if it still belongs to this task.

    Simulates another task reclaiming the (stale) lock while this one runs; the finishing
    task must not clear the usurper's lock.
    """

    def take_over(document_source):
        usurper = DocumentSource.objects.get(id=document_source.id)
        usurper.sync_task_id = "usurper-task"
        usurper.save(update_fields=["sync_task_id"])
        return SyncResult(success=True)

    sync_mock.side_effect = take_over
    source = DocumentSourceFactory.create()

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    source.refresh_from_db()
    assert source.sync_task_id == "usurper-task"


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_task_reclaims_lock_without_start_time(sync_mock):
    """A lock with no recorded start time (e.g. left by pre-sync_started_at code) is reclaimable."""
    sync_mock.return_value = SyncResult(success=True)
    source = DocumentSourceFactory.create(sync_task_id="legacy-task", sync_started_at=None)
    assert source.is_sync_in_progress is False

    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    sync_mock.assert_called_once()
    source.refresh_from_db()
    assert source.sync_task_id == ""


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source_task")
def test_auto_sync_skips_fresh_lock_but_includes_stale(mock_sync_task):
    """Scheduler skips sources with a fresh lock but re-dispatches stale-locked ones so they self-heal."""
    index = CollectionFactory.create(is_index=True)
    unlocked = DocumentSourceFactory.create(collection=index, source_type="github", auto_sync_enabled=True)
    DocumentSourceFactory.create(
        collection=index,
        source_type="github",
        auto_sync_enabled=True,
        sync_task_id="running",
        sync_started_at=timezone.now(),
    )
    stale = DocumentSourceFactory.create(
        collection=index,
        source_type="github",
        auto_sync_enabled=True,
        sync_task_id="dead",
        sync_started_at=timezone.now() - SYNC_LOCK_TIMEOUT - timedelta(minutes=1),
    )
    no_start_time = DocumentSourceFactory.create(
        collection=index,
        source_type="github",
        auto_sync_enabled=True,
        sync_task_id="legacy",
        sync_started_at=None,
    )

    sync_all_document_sources_task()

    dispatched_ids = set(mock_sync_task.map.call_args.args[0])
    assert dispatched_ids == {unlocked.id, stale.id, no_start_time.id}


@pytest.mark.django_db()
@patch("apps.documents.tasks.sync_document_source")
def test_sync_document_source_task_aborts_when_source_deleted(sync_mock):
    """The sync task aborts cleanly when the source is deleted mid-sync.

    Regression: DocumentSourceDeleted must be swallowed (not crash the task). The lock is
    still released in the finally via a queryset update, which is a safe no-op if the row
    is truly gone.
    """
    source = DocumentSourceFactory.create(source_type="github", auto_sync_enabled=True)
    sync_mock.side_effect = DocumentSourceDeleted(source.id)

    # Must not raise despite the source disappearing mid-sync.
    sync_document_source_task.apply(args=[source.id], task_id="task-a")

    sync_mock.assert_called_once()
    source.refresh_from_db()
    assert source.sync_task_id == ""
    assert source.sync_started_at is None


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_integrity_error_does_not_retry(progress_recorder_mock):
    """ZipIntegrityError is not retried — task fails immediately on size mismatch."""
    collection = CollectionFactory.create(name="integrity-error-collection")
    team = collection.team
    file1 = FileFactory.create(team=team, name="file1.txt", file=ContentFile(b"data", name="file1.txt"))
    File.objects.filter(id=file1.id).update(content_size=1024)
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    retry_mock = MagicMock()

    @contextmanager
    def open_returns_wrong_size(*args, **kwargs):
        yield BytesIO(b"data")

    with patch("django.db.models.fields.files.FieldFile.open", open_returns_wrong_size):
        with patch.object(create_collection_zip_task, "retry", retry_mock):
            with pytest.raises(ZipIntegrityError):
                create_collection_zip_task(collection.id, team.id)

    retry_mock.assert_not_called()


@pytest.mark.django_db()
def test_async_create_collection_version_creates_snapshot():
    collection = CollectionFactory.create(is_index=True)
    assert not collection.versions.exists()

    async_create_collection_version(collection.id)

    collection.refresh_from_db()
    snapshots = collection.versions.all()
    assert snapshots.count() == 1
    assert snapshots.first().is_a_version
    assert collection.create_version_task_id == ""


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.create_new_version", side_effect=RuntimeError("boom"))
def test_async_create_collection_version_clears_task_id_on_failure(create_new_version_mock):
    collection = CollectionFactory.create(is_index=True, create_version_task_id="in-flight")

    with pytest.raises(RuntimeError):
        async_create_collection_version(collection.id)

    collection.refresh_from_db()
    assert collection.create_version_task_id == ""
