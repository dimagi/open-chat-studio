from unittest.mock import ANY, patch

import pytest

from apps.documents.models import CollectionFile, FileStatus
from apps.documents.tasks import (
    index_collection_files_task,
    migrate_vector_stores,
)
from apps.utils.factories.documents import CollectionFactory
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
