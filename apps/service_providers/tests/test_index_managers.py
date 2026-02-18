from unittest import mock

import openai
import pytest
from django.conf import settings

from apps.documents.exceptions import FileUploadError
from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import FileChunkEmbedding
from apps.service_providers.exceptions import UnableToLinkFileException
from apps.service_providers.llm_service.index_managers import (
    LocalIndexManager,
    OpenAILocalIndexManager,
    OpenAIRemoteIndexManager,
    RemoteIndexManager,
)
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def provider_client_mock():
    """Mock OpenAI client"""
    return mock.Mock()


@pytest.fixture()
def local_index_instance(db):
    return CollectionFactory(is_index=True, is_remote_index=False)


@pytest.fixture()
def remote_collection_index(db):
    return CollectionFactory(is_index=True, is_remote_index=True)


class LocalIndexManagerMock(LocalIndexManager):
    def chunk_file(self, file, chunk_size=None, chunk_overlap=None):
        return ["test", "content"]

    def get_embedding_vector(self, text):
        """Mock method to return a fixed embedding vector"""
        return [0.1] * settings.EMBEDDING_VECTOR_SIZE


class RemoteIndexManagerMock(RemoteIndexManager):
    def get(self): ...

    def delete_remote_index(self): ...

    def delete_files_from_index(self, *args, **kwargs): ...

    def link_files_to_remote_index(self, *args, **kwargs): ...
    def file_exists_at_remote(self, *args, **kwargs) -> bool:
        return False

    def upload_file_to_remote(self, *args, **kwargs): ...

    def delete_files(self, *args, **kwargs): ...


@pytest.mark.django_db()
class TestLocalIndexManager:
    @pytest.fixture()
    def index_manager(self, provider_client_mock):
        with mock.patch("apps.service_providers.models.LlmProvider.get_local_index_manager") as get_local_index_manager:
            manager = LocalIndexManagerMock(api_key="api-123", embedding_model_name="embedding-model")
            get_local_index_manager.return_value = manager
            yield manager

    def test_add_files_success(self, local_index_instance, index_manager):
        file = FileFactory()
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(file, "read_content", return_value="test content"):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        # Verify that embeddings were created
        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance)
        assert embeddings.count() == 2
        assert embeddings.first().text == "test"
        assert embeddings.last().text == "content"

    def test_add_files_fails(self, local_index_instance, index_manager):
        """If anything goes wrong during local indexing, the file should be marked as failed"""
        file = FileFactory()
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock file.read_content to raise an exception
        with mock.patch.object(index_manager, "chunk_file", side_effect=Exception("Read failed")):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED

    def test_delete_embeddings(self, local_index_instance):
        file = FileFactory()
        embedding = FileChunkEmbedding.objects.create(
            team=file.team,
            file=file,
            collection=local_index_instance,
            chunk_number=1,
            page_number=1,
            text="test embedding",
            embedding=[0.1] * settings.EMBEDDING_VECTOR_SIZE,
        )
        local_index_instance.get_index_manager().delete_embeddings(file.id)

        with pytest.raises(FileChunkEmbedding.DoesNotExist):
            embedding.refresh_from_db()


@pytest.mark.django_db()
class TestRemoteIndexManager:
    @pytest.fixture()
    def index_manager(self):
        with mock.patch(
            "apps.service_providers.models.LlmProvider.get_remote_index_manager"
        ) as get_remote_index_manager:
            manager = RemoteIndexManagerMock(index_id="remote-index-123")
            get_remote_index_manager.return_value = manager
            yield manager

    def test_add_files_success(self, remote_collection_index, index_manager):
        file = FileFactory(external_id="test_file_id_3")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

    def test_add_files_with_file_upload_failures(self, remote_collection_index, index_manager):
        file = FileFactory()
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock ensure_remote_file_exists to raise FileUploadError
        with mock.patch.object(
            index_manager, "_ensure_remote_file_exists", side_effect=FileUploadError("Upload failed")
        ):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            remote_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED

    def test_add_files_with_linking_failures(self, remote_collection_index, index_manager):
        file = FileFactory(external_id="test_file_id")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock successful upload but failed linking
        with mock.patch.object(
            index_manager, "link_files_to_remote_index", side_effect=UnableToLinkFileException("Link failed")
        ):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            remote_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED


@pytest.mark.django_db()
class TestOpenAIRemoteIndexManager:
    @pytest.fixture()
    def index_manager(self, provider_client_mock):
        """Create OpenAIRemoteIndexManager instance with mocked client"""
        return OpenAIRemoteIndexManager(client=provider_client_mock, index_id="vs-test-123")

    def test_get(self, index_manager, provider_client_mock):
        """Test retrieving vector store from remote index"""
        mock_vector_store = mock.Mock()
        provider_client_mock.vector_stores.retrieve.return_value = mock_vector_store

        result = index_manager.get()

        provider_client_mock.vector_stores.retrieve.assert_called_once_with("vs-test-123")
        assert result == mock_vector_store

    def test_delete_remote_index_success(self, index_manager, provider_client_mock):
        """Test successful deletion of vector store"""
        index_manager.delete_remote_index()
        provider_client_mock.vector_stores.delete.assert_called_once_with(vector_store_id="vs-test-123")

    def test_delete_file_success(self, index_manager, provider_client_mock):
        """Test successful file deletion from vector store"""
        index_manager.delete_file_from_index("file-123")

        provider_client_mock.vector_stores.files.delete.assert_called_once_with(
            vector_store_id="vs-test-123", file_id="file-123"
        )

    def test_delete_file_not_found(self, index_manager, provider_client_mock):
        """Test file deletion when file not found"""
        provider_client_mock.vector_stores.files.delete.side_effect = openai.NotFoundError(
            "File not found", response=mock.Mock(), body={}
        )

        # Should not raise exception, just log warning
        index_manager.delete_file_from_index("file-123")

        provider_client_mock.vector_stores.files.delete.assert_called_once_with(
            vector_store_id="vs-test-123", file_id="file-123"
        )

    def testlink_files_to_remote_index_success(self, index_manager, provider_client_mock):
        """Test successful linking of files to vector store"""
        index_manager.link_files_to_remote_index(["file-1", "file-2"])

        provider_client_mock.vector_stores.file_batches.create.assert_called_once_with(
            vector_store_id="vs-test-123", file_ids=["file-1", "file-2"], chunking_strategy=None
        )

    def testlink_files_to_remote_index_with_chunking_strategy(self, index_manager, provider_client_mock):
        """Test linking files with chunking strategy"""
        index_manager.link_files_to_remote_index(["file-1", "file-2"], chunk_size=1000, chunk_overlap=200)

        expected_chunking_strategy = {
            "type": "static",
            "static": {"max_chunk_size_tokens": 1000, "chunk_overlap_tokens": 200},
        }
        provider_client_mock.vector_stores.file_batches.create.assert_called_once_with(
            vector_store_id="vs-test-123", file_ids=["file-1", "file-2"], chunking_strategy=expected_chunking_strategy
        )

    @mock.patch("apps.service_providers.llm_service.index_managers.chunk_list")
    def testlink_files_to_remote_index_large_batch(self, mock_chunk_list, index_manager, provider_client_mock):
        """Test linking large number of files with batching"""
        file_ids = [f"file-{i}" for i in range(1000)]
        mock_chunk_list.return_value = [file_ids[:500], file_ids[500:]]

        index_manager.link_files_to_remote_index(file_ids)

        mock_chunk_list.assert_called_once_with(file_ids, 500)
        assert provider_client_mock.vector_stores.file_batches.create.call_count == 2

    def testlink_files_to_remote_index_failure(self, index_manager, provider_client_mock):
        """Test linking files failure"""
        provider_client_mock.vector_stores.file_batches.create.side_effect = Exception("Connection error")

        with pytest.raises(UnableToLinkFileException) as exc_info:
            index_manager.link_files_to_remote_index(["file-1", "file-2"])

        assert "Failed to link files to OpenAI vector store" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("file_external_id", "remote_file_exists", "create_file_called"),
        [
            ("", False, True),
            ("ext-id-123", False, True),
            ("ext-id-123", True, False),
        ],
    )
    @mock.patch("apps.assistants.sync.create_files_remote")
    @mock.patch("apps.service_providers.llm_service.index_managers.OpenAIRemoteIndexManager.file_exists_at_remote")
    def test_ensure_remote_file_exists(
        self,
        file_exists_at_remote,
        mock_create_files_remote,
        file_external_id,
        remote_file_exists,
        create_file_called,
        index_manager,
    ):
        """Test uploading file when it doesn't have external_id"""
        file_exists_at_remote.return_value = remote_file_exists
        file = FileFactory.build(external_id=file_external_id)

        index_manager._ensure_remote_file_exists(file)

        if create_file_called:
            mock_create_files_remote.assert_called()
        else:
            mock_create_files_remote.assert_not_called()

    def test_delete_files_success(self, index_manager, provider_client_mock):
        """Test successful deletion of multiple files"""
        file1 = FileFactory(external_id="file-1")
        file2 = FileFactory(external_id="file-2")
        files = [file1, file2]

        index_manager.delete_files(files)

        # Verify files.delete was called for each file
        assert provider_client_mock.files.delete.call_count == 2
        provider_client_mock.files.delete.assert_any_call("file-1")
        provider_client_mock.files.delete.assert_any_call("file-2")

        # Verify external_id was cleared
        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.external_id == ""
        assert file2.external_id == ""

    def test_delete_files_with_not_found_error(self, index_manager, provider_client_mock):
        """Test deletion of files when some files are not found"""
        file1 = FileFactory(external_id="file-1")
        file2 = FileFactory(external_id="file-2")
        files = [file1, file2]

        # First call succeeds, second call raises NotFoundError
        provider_client_mock.files.delete.side_effect = [
            None,
            openai.NotFoundError("Not found", response=mock.Mock(), body={}),
        ]

        # Should not raise exception due to contextlib.suppress
        index_manager.delete_files(files)

        # Verify external_id was cleared for both files
        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.external_id == ""
        assert file2.external_id == ""


class TestOpenAILocalIndexManager:
    @pytest.fixture()
    def index_manager(self, provider_client_mock):
        """Create OpenAIRemoteIndexManager instance with mocked client"""
        return OpenAILocalIndexManager(api_key="api-123", embedding_model_name="embedding-model")

    def test_chunk_content(self, index_manager):
        file = mock.Mock()
        file.read_content = lambda: "This is test content."
        response = index_manager.chunk_file(file, chunk_size=2, chunk_overlap=0)
        assert response == ["This is", "test", "c", "on", "te", "nt", "."]
