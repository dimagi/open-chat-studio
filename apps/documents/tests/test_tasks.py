from unittest.mock import ANY, Mock, patch

import pytest

from apps.assistants.sync import OpenAiSyncError
from apps.documents.models import CollectionFile, FileStatus
from apps.documents.tasks import (
    _upload_files_to_vector_store,
    index_collection_files_task,
    migrate_vector_stores,
)
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.fixture()
def mock_vector_store_manager():
    with patch("apps.service_providers.models.LlmProvider.get_index_manager") as get_index_manager:
        manager_instance = Mock()
        manager_instance.client = Mock()
        get_index_manager.return_value = manager_instance
        yield manager_instance


@pytest.fixture()
def collection(db):
    llm_provider = LlmProviderFactory(name="test-provider")
    return CollectionFactory(name="test-collection", llm_provider=llm_provider, openai_vector_store_id="vs_123")


@pytest.fixture()
def collection_file(db, collection):
    file = FileFactory(name="test.txt", team=collection.team)
    return CollectionFile.objects.create(
        file=file,
        collection=collection,
        status=FileStatus.PENDING,
        metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 100}},
    )


@pytest.mark.django_db()
class TestUploadFilesToVectorStore:
    @patch("apps.documents.tasks.create_files_remote")
    def test_upload_files_task_success(self, create_files_remote, collection_file, mock_vector_store_manager):
        """Test successful file upload to vector store"""
        create_files_remote.return_value = ["ext-file-id"]
        mock_vector_store_manager.link_files_to_vector_store.return_value = "vs_123"

        index_collection_files_task(collection_file.collection_id)
        # Verify file status was updated
        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        # Verify vector store interactions
        mock_vector_store_manager.link_files_to_vector_store.assert_called_once_with(
            vector_store_id="vs_123", file_ids=["ext-file-id"], chunk_size=1000, chunk_overlap=100
        )

    @patch("apps.documents.tasks.create_files_remote")
    def test_upload_files_task_failure(self, create_files_remote, collection_file, mock_vector_store_manager):
        """Test handling of upload failures"""
        create_files_remote.side_effect = Exception("Upload failed")

        index_collection_files_task(collection_file.collection_id)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED

    @patch("apps.documents.tasks.create_files_remote")
    def test_upload_files_task_openai_sync_error(self, create_files_remote, collection_file, mock_vector_store_manager):
        """Test handling of OpenAiSyncError during file upload"""
        mock_vector_store_manager.link_files_to_vector_store.side_effect = OpenAiSyncError("Failed to sync with OpenAI")

        index_collection_files_task(collection_file.collection_id)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED


@pytest.mark.django_db()
class TestMigrateVectorStores:
    @patch("apps.documents.tasks.create_files_remote")
    @patch("apps.documents.tasks._cleanup_old_vector_store")
    @patch("apps.documents.tasks._upload_files_to_vector_store")
    def test_successful_migration(
        self,
        upload_files_to_vector_store,
        cleanup_old_vector_store,
        create_files_remote,
        collection,
        mock_vector_store_manager,
    ):
        """Test successful migration of files between vector stores"""
        # New file ids are returned when uploaded to the new provider
        create_files_remote.side_effect = ["new-file-id1", "new-file-id2"]

        old_vector_store_id = "old_vs_123"
        new_vector_store_id = "new_vs_123"

        # The new vector store should already be created, but the files should still reference the old provider-assigned
        # ids
        collection.openai_vector_store_id = new_vector_store_id
        collection.save()
        old_llm_provider = LlmProviderFactory(team=collection.team)

        col_file_1 = CollectionFile.objects.create(
            file=FileFactory(team=collection.team, external_id="old-file-id1"),
            collection=collection,
            status=FileStatus.PENDING,
            metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 100}},
        )
        col_file_2 = CollectionFile.objects.create(
            file=FileFactory(team=collection.team, external_id="old-file-id2"),
            collection=collection,
            status=FileStatus.PENDING,
            metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 500}},
        )

        # Run migration task
        migrate_vector_stores(
            collection_id=collection.id,
            from_vector_store_id=old_vector_store_id,
            from_llm_provider_id=old_llm_provider.id,
        )

        # Verify vector store and file cleanup
        cleanup_old_vector_store.assert_called_once_with(
            old_llm_provider.id, old_vector_store_id, ["old-file-id1", "old-file-id2"]
        )

        # Verify file uploads to new vector store
        upload_files_to_vector_store.assert_any_call(ANY, collection, [col_file_1], chunk_size=1000, chunk_overlap=100)
        upload_files_to_vector_store.assert_any_call(ANY, collection, [col_file_2], chunk_size=1000, chunk_overlap=500)

    def test_migration_with_multiple_chunking_strategies(self, collection, mock_vector_store_manager):
        """Test migration handles multiple files with different chunking strategies"""
        # Create files with different chunking strategies
        file1 = File.objects.create(name="test1.txt", team=collection.team)
        file2 = File.objects.create(name="test2.txt", team=collection.team)

        CollectionFile.objects.create(
            file=file1,
            collection=collection,
            status=FileStatus.PENDING,
            metadata={"chunking_strategy": {"chunk_size": 1000, "chunk_overlap": 100}},
        )
        CollectionFile.objects.create(
            file=file2,
            collection=collection,
            status=FileStatus.PENDING,
            metadata={"chunking_strategy": {"chunk_size": 2000, "chunk_overlap": 200}},
        )

        migrate_vector_stores(
            collection_id=collection.id,
            from_vector_store_id="old_vs_123",
            from_llm_provider_id=collection.llm_provider.id,
        )

        # Verify that files were processed in separate groups by chunking strategy
        assert mock_vector_store_manager.link_files_to_vector_store.call_count == 2


@pytest.mark.django_db()
class TestUploadFilesToVectorStoreHelper:
    @patch("apps.documents.tasks.create_files_remote")
    def test_helper_success(self, create_files_remote, collection, collection_file, mock_vector_store_manager):
        """Test the helper function handles successful uploads"""
        create_files_remote.return_value = ["ext-file-id"]
        mock_vector_store_manager.link_files_to_vector_store.return_value = "vs_123"

        _upload_files_to_vector_store(
            mock_vector_store_manager.client, collection, [collection_file], chunk_size=1000, chunk_overlap=100
        )

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        mock_vector_store_manager.link_files_to_vector_store.assert_called_once()

    @patch("apps.documents.tasks.create_files_remote")
    def test_helper_handles_errors(self, create_files_remote, collection, collection_file, mock_vector_store_manager):
        """Test the helper function handles upload errors properly"""
        create_files_remote.side_effect = Exception("Upload failed")

        _upload_files_to_vector_store(
            mock_vector_store_manager.client, collection, [collection_file], chunk_size=1000, chunk_overlap=100
        )

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
