import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import ANY, patch

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.documents.models import CollectionFile, FileStatus
from apps.documents.tasks import (
    create_collection_zip_task,
    index_collection_files_task,
    migrate_vector_stores,
)
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.fixture()
def collection(db):
    llm_provider = LlmProviderFactory(name="test-provider")
    return CollectionFactory(
        name="test-collection",
        llm_provider=llm_provider,
        openai_vector_store_id="vs_123",
        is_index=True,
        is_remote_index=True,
    )


@pytest.mark.django_db()
@patch("apps.documents.models.Collection.add_files_to_index")
def test_collection_files_grouped_by_chunking_strategy(add_files_to_index_mock, collection):
    """Test that collection files are grouped by chunking strategy"""
    col_file_1 = CollectionFile.objects.create(
        file=FileFactory(team=collection.team),
        collection=collection,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}},
    )
    col_file_2 = CollectionFile.objects.create(
        file=FileFactory(team=collection.team),
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
    previous_llm_provider = LlmProviderFactory(name="old-provider")

    file = FileFactory(team=collection.team, external_id="old-file-id")
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
    collection = CollectionFactory(name="test-collection")
    team = collection.team

    # Create manually uploaded files (no document_source)
    file1 = FileFactory(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    file2 = FileFactory(team=team, name="file2.pdf", file=ContentFile(b"Content of file 2", name="file2.pdf"))
    file3 = FileFactory(team=team, name="file3.docx", file=ContentFile(b"Content of file 3", name="file3.docx"))

    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file2, collection=collection, document_source=None)
    CollectionFile.objects.create(file=file3, collection=collection, document_source=None)

    # Create a file with a document source (should be excluded)
    document_source = DocumentSourceFactory(collection=collection, team=team)
    file4 = FileFactory(team=team, name="file4.txt", file=ContentFile(b"Content of file 4", name="file4.txt"))
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
    collection = CollectionFactory(name="empty-collection")
    team = collection.team

    # Create only files with document sources (should be excluded)
    document_source = DocumentSourceFactory(collection=collection, team=team)
    file1 = FileFactory(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=document_source)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is None
    logger_mock.warning.assert_called_once()
    assert f"No manually uploaded files found in collection {collection.id}" in str(logger_mock.warning.call_args)


@pytest.mark.django_db()
@patch("apps.documents.tasks.ProgressRecorder")
def test_create_collection_zip_task_handles_duplicate_filenames(progress_recorder_mock):
    """handles duplicate filenames by appending a numerical suffix."""
    collection = CollectionFactory(name="duplicate-collection")
    team = collection.team

    # Create files with duplicate names
    file1 = FileFactory(team=team, name="document.txt", file=ContentFile(b"First document", name="document.txt"))
    file2 = FileFactory(team=team, name="document.txt", file=ContentFile(b"Second document", name="document.txt"))
    file3 = FileFactory(team=team, name="document.txt", file=ContentFile(b"Third document", name="document.txt"))

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
@patch("apps.documents.tasks.timezone")
def test_create_collection_zip_task_sets_expiry_date(timezone_mock, progress_recorder_mock):
    collection = CollectionFactory(name="expiry-test-collection")
    team = collection.team

    # Mock timezone to get predictable expiry date
    mock_now = timezone.now()
    timezone_mock.now.return_value = mock_now

    file1 = FileFactory(team=team, name="file1.txt", file=ContentFile(b"Content of file 1", name="file1.txt"))
    CollectionFile.objects.create(file=file1, collection=collection, document_source=None)

    result = create_collection_zip_task(collection.id, team.id)

    assert result is not None
    zip_file_obj = File.objects.get(id=result)
    expected_expiry = mock_now + timedelta(hours=24)
    assert zip_file_obj.expiry_date == expected_expiry
