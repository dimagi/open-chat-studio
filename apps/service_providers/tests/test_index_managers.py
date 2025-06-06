from unittest import mock

import openai
import pytest

from apps.service_providers.exceptions import UnableToLinkFileException
from apps.service_providers.llm_service.index_managers import OpenAILocalIndexManager, OpenAIRemoteIndexManager
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def client_mock():
    """Mock OpenAI client"""
    return mock.Mock()


@pytest.mark.django_db()
class TestOpenAIRemoteIndexManager:
    @pytest.fixture()
    def index_manager(self, client_mock):
        """Create OpenAIRemoteIndexManager instance with mocked client"""
        return OpenAIRemoteIndexManager(client=client_mock, index_id="vs-test-123")

    def test_get(self, index_manager, client_mock):
        """Test retrieving vector store from remote index"""
        mock_vector_store = mock.Mock()
        client_mock.vector_stores.retrieve.return_value = mock_vector_store

        result = index_manager.get()

        client_mock.vector_stores.retrieve.assert_called_once_with("vs-test-123")
        assert result == mock_vector_store

    def test_create_vector_store_without_file_ids(self, index_manager, client_mock):
        """Test creating vector store without file IDs"""
        mock_vector_store = mock.Mock()
        mock_vector_store.id = "vs-new-456"
        client_mock.vector_stores.create.return_value = mock_vector_store

        result = index_manager.create_remote_index("Test Vector Store")

        client_mock.vector_stores.create.assert_called_once_with(name="Test Vector Store", file_ids=[])
        assert result == "vs-new-456"
        assert index_manager.index_id == "vs-new-456"

    def test_create_vector_store_with_file_ids(self, index_manager, client_mock):
        """Test creating vector store with file IDs"""
        mock_vector_store = mock.Mock()
        mock_vector_store.id = "vs-new-789"
        client_mock.vector_stores.create.return_value = mock_vector_store

        result = index_manager.create_remote_index("Test Vector Store", file_ids=["file-1", "file-2"])

        client_mock.vector_stores.create.assert_called_once_with(
            name="Test Vector Store", file_ids=["file-1", "file-2"]
        )
        assert result == "vs-new-789"
        assert index_manager.index_id == "vs-new-789"

    def test_delete_vector_store_success(self, index_manager, client_mock):
        """Test successful deletion of vector store"""
        index_manager.delete_vector_store()

        client_mock.vector_stores.delete.assert_called_once_with(vector_store_id="vs-test-123")

    @pytest.mark.parametrize("fail_silently", [False, True])
    def test_delete_vector_store_with_error(self, fail_silently, index_manager, client_mock):
        client_mock.vector_stores.delete.side_effect = Exception("Some error occurred")

        if fail_silently:
            index_manager.delete_vector_store(fail_silently=True)
        else:
            with pytest.raises(Exception, match="Some error occurred"):
                index_manager.delete_vector_store(fail_silently=False)

    def test_delete_file_success(self, index_manager, client_mock):
        """Test successful file deletion from vector store"""
        index_manager.delete_file_from_index("file-123")

        client_mock.vector_stores.files.delete.assert_called_once_with(
            vector_store_id="vs-test-123", file_id="file-123"
        )

    def test_delete_file_not_found(self, index_manager, client_mock):
        """Test file deletion when file not found"""
        client_mock.vector_stores.files.delete.side_effect = openai.NotFoundError(
            "File not found", response=mock.Mock(), body={}
        )

        # Should not raise exception, just log warning
        index_manager.delete_file_from_index("file-123")

        client_mock.vector_stores.files.delete.assert_called_once_with(
            vector_store_id="vs-test-123", file_id="file-123"
        )

    def test_link_files_to_remote_index_success(self, index_manager, client_mock):
        """Test successful linking of files to vector store"""
        index_manager.link_files_to_remote_index(["file-1", "file-2"])

        client_mock.vector_stores.file_batches.create.assert_called_once_with(
            vector_store_id="vs-test-123", file_ids=["file-1", "file-2"], chunking_strategy=None
        )

    def test_link_files_to_remote_index_with_chunking_strategy(self, index_manager, client_mock):
        """Test linking files with chunking strategy"""
        index_manager.link_files_to_remote_index(["file-1", "file-2"], chunk_size=1000, chunk_overlap=200)

        expected_chunking_strategy = {
            "type": "static",
            "static": {"max_chunk_size_tokens": 1000, "chunk_overlap_tokens": 200},
        }
        client_mock.vector_stores.file_batches.create.assert_called_once_with(
            vector_store_id="vs-test-123", file_ids=["file-1", "file-2"], chunking_strategy=expected_chunking_strategy
        )

    @mock.patch("apps.service_providers.llm_service.index_managers.chunk_list")
    def test_link_files_to_remote_index_large_batch(self, mock_chunk_list, index_manager, client_mock):
        """Test linking large number of files with batching"""
        file_ids = [f"file-{i}" for i in range(1000)]
        mock_chunk_list.return_value = [file_ids[:500], file_ids[500:]]

        index_manager.link_files_to_remote_index(file_ids)

        mock_chunk_list.assert_called_once_with(file_ids, 500)
        assert client_mock.vector_stores.file_batches.create.call_count == 2

    def test_link_files_to_remote_index_failure(self, index_manager, client_mock):
        """Test linking files failure"""
        client_mock.vector_stores.file_batches.create.side_effect = Exception("Connection error")

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

        index_manager.ensure_remote_file_exists(file)

        if create_file_called:
            mock_create_files_remote.assert_called()
        else:
            mock_create_files_remote.assert_not_called()

    def test_delete_files_success(self, index_manager, client_mock):
        """Test successful deletion of multiple files"""
        file1 = FileFactory(external_id="file-1")
        file2 = FileFactory(external_id="file-2")
        files = [file1, file2]

        index_manager.delete_files(files)

        # Verify files.delete was called for each file
        assert client_mock.files.delete.call_count == 2
        client_mock.files.delete.assert_any_call("file-1")
        client_mock.files.delete.assert_any_call("file-2")

        # Verify external_id was cleared
        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.external_id == ""
        assert file2.external_id == ""

    def test_delete_files_with_not_found_error(self, index_manager, client_mock):
        """Test deletion of files when some files are not found"""
        file1 = FileFactory(external_id="file-1")
        file2 = FileFactory(external_id="file-2")
        files = [file1, file2]

        # First call succeeds, second call raises NotFoundError
        client_mock.files.delete.side_effect = [None, openai.NotFoundError("Not found", response=mock.Mock(), body={})]

        # Should not raise exception due to contextlib.suppress
        index_manager.delete_files(files)

        # Verify external_id was cleared for both files
        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.external_id == ""
        assert file2.external_id == ""


class TestOpenAILocalIndexManager:
    @pytest.fixture()
    def index_manager(self, client_mock):
        """Create OpenAIRemoteIndexManager instance with mocked client"""
        return OpenAILocalIndexManager(client=client_mock, embedding_model_name="embedding-model")

    def test_chunk_content(self, index_manager):
        file = mock.Mock()
        file.read_content = lambda: "This is test content."
        response = index_manager.chunk_file(file, chunk_size=2, chunk_overlap=0)
        assert response == ["This is", "test", "c", "on", "te", "nt", "."]
