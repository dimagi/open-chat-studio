from unittest import mock

import openai
import pytest
from django.conf import settings
from django.db import DatabaseError

from apps.documents.exceptions import FileUploadError
from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import FileChunkEmbedding
from apps.service_providers.exceptions import UnableToLinkFileException
from apps.service_providers.llm_service.index_managers import (
    GoogleLocalIndexManager,
    LocalIndexManager,
    OpenAILocalIndexManager,
    OpenAIRemoteIndexManager,
    RemoteIndexManager,
    VoyageAILocalIndexManager,
)
from apps.service_providers.llm_service.main import OpenAILlmService
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def provider_client_mock():
    """Mock OpenAI client"""
    return mock.Mock()


@pytest.fixture()
def local_index_instance(db):
    return CollectionFactory.create(is_index=True, is_remote_index=False)


@pytest.fixture()
def remote_collection_index(db):
    return CollectionFactory.create(is_index=True, is_remote_index=True)


class LocalIndexManagerMock(LocalIndexManager):
    def chunk_file(self, text, chunk_size=None, chunk_overlap=None):
        return ["test", "content"]

    def get_embedding_vector(self, text, *, input_type):  # ty: ignore[invalid-method-override]
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
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        # Verify that embeddings were created
        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance)
        assert embeddings.count() == 2
        assert embeddings.first().text == "test"
        assert embeddings.last().text == "content"

    def test_search_vector_failure_keeps_the_embeddings(self, local_index_instance, index_manager):
        """Building the lexical vectors is the last step and each embedding above it cost a provider
        call, so a failure there must not take the file's embeddings down with it. Keyword search
        loses the file until it is re-indexed; dense retrieval is unaffected.
        """
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(LocalIndexManager, "_build_search_vectors", side_effect=DatabaseError("boom")):
            index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED
        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance)
        assert embeddings.count() == 2
        assert all(embedding.search_vector is None for embedding in embeddings)

    def test_add_files_fails(self, local_index_instance, index_manager):
        """If anything goes wrong during local indexing, the file should be marked as failed"""
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        with mock.patch.object(index_manager, "chunk_file", side_effect=Exception("Read failed")):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED

    def test_add_files_records_the_failure_reason(self, local_index_instance, index_manager):
        """The exception that caused the failure is the only place the reason exists, so it is
        captured there rather than reconstructed later from the status."""
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(
            index_manager, "chunk_file", side_effect=ValueError("Error code: 401 - Incorrect API key provided")
        ):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert collection_file.failure_reason == "ValueError: Error code: 401 - Incorrect API key provided"

    def test_add_files_clears_the_failure_reason_on_success(self, local_index_instance, index_manager):
        """A reason left by an earlier attempt describes an outcome that no longer holds."""
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)
        collection_file.status = FileStatus.FAILED
        collection_file.failure_reason = "ValueError: stale reason from the previous attempt"
        collection_file.save()

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED
        assert collection_file.failure_reason == ""

    def test_add_files_calls_get_embedding_vector_with_document_input_type(self, local_index_instance, index_manager):
        file = FileFactory.create(file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(
            index_manager,
            "get_embedding_vector",
            wraps=index_manager.get_embedding_vector,
        ) as spy:
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        assert spy.call_count == 2
        for call in spy.call_args_list:
            assert call.kwargs == {"input_type": "document"} or call.args[1:] == ("document",)

    def test_add_files_skips_chunks_that_are_empty_after_nul_stripping(self, local_index_instance, index_manager):
        """A chunk made up only of NUL bytes becomes "" after sanitization. Skip it so the
        embedder is never called with an empty string (Voyage raises; OpenAI/Google reject
        via the API) and no partial chunks are persisted for a file that then fails."""
        file = FileFactory.create(file__data=b"irrelevant")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with (
            mock.patch.object(index_manager, "chunk_file", return_value=["test", "\x00\x00", "content"]),
            mock.patch.object(
                index_manager,
                "get_embedding_vector",
                wraps=index_manager.get_embedding_vector,
            ) as spy,
        ):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance).order_by(
            "chunk_number"
        )
        assert embeddings.count() == 2
        assert list(embeddings.values_list("text", flat=True)) == ["test", "content"]

        assert spy.call_count == 2
        embedded_texts = [call.args[0] for call in spy.call_args_list]
        assert "" not in embedded_texts

    def test_add_files_fails_when_no_text_can_be_extracted(self, local_index_instance, index_manager):
        """Source files are stored unparsed, so an image-only PDF now reaches indexing.

        Extracting nothing from it leaves a file with no embeddings, which is indistinguishable
        from a successfully indexed file unless it is recorded as failed.
        """
        file = FileFactory.create(file__data=b"   \n  ")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert "no text" in collection_file.failure_reason.lower()
        assert not FileChunkEmbedding.objects.filter(file=file).exists()

    def test_add_files_fails_when_every_chunk_sanitizes_away(self, local_index_instance, index_manager):
        """NUL-only content clears the empty-text check but leaves nothing to embed.

        Postgres cannot store NUL bytes, so every chunk sanitizes down to "" and is skipped.
        A file with no embeddings is indistinguishable from one that indexed cleanly unless
        it is recorded as failed.
        """
        file = FileFactory.create(file__data=b"\x00\x00\x00")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(index_manager, "chunk_file", return_value=["\x00\x00\x00"]):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert "no text" in collection_file.failure_reason.lower()
        assert not FileChunkEmbedding.objects.filter(file=file).exists()

    def test_one_unextractable_file_does_not_stop_the_batch(self, local_index_instance, index_manager):
        """A batch is one Celery task, so a file that cannot be read must not abort the rest."""
        good_one = FileFactory.create(file__data=b"first document")
        unextractable = FileFactory.create(file__data=b"   \n  ")
        good_two = FileFactory.create(file__data=b"second document")
        for file in (good_one, unextractable, good_two):
            local_index_instance.files.add(file)

        collection_files = CollectionFile.objects.filter(
            collection=local_index_instance, file__in=[good_one, unextractable, good_two]
        )
        index_manager.add_files(collection_files.order_by("file_id").iterator(1))

        statuses = {cf.file_id: cf.status for cf in collection_files}
        assert statuses[good_one.id] == FileStatus.COMPLETED
        assert statuses[unextractable.id] == FileStatus.FAILED
        assert statuses[good_two.id] == FileStatus.COMPLETED
        assert FileChunkEmbedding.objects.filter(file=good_two).count() == 2
        assert not FileChunkEmbedding.objects.filter(file=unextractable).exists()

    def test_query_calls_get_embedding_vector_with_query_input_type(self, local_index_instance, index_manager):
        with mock.patch.object(
            index_manager,
            "get_embedding_vector",
            wraps=index_manager.get_embedding_vector,
        ) as spy:
            index_manager.query(index_id=local_index_instance.id, query="a question")

        assert spy.call_count == 1
        call = spy.call_args_list[0]
        assert call.kwargs == {"input_type": "query"} or call.args[1:] == ("query",)

    def test_delete_embeddings(self, local_index_instance):
        file = FileFactory.create()
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
class TestLocalIndexManagerContextualization:
    """Tests for the contextual retrieval wiring in add_files (issue #2681)."""

    @pytest.fixture()
    def contextualizing_index_manager(self):
        contextualizer = mock.Mock()
        contextualizer.get_context.return_value = "Source document: annual_report.pdf."
        return LocalIndexManagerMock(
            api_key="api-123",
            embedding_model_name="embedding-model",
            contextualizer=contextualizer,
        )

    def test_context_stored_when_contextualizer_set(self, local_index_instance, contextualizing_index_manager):
        file = FileFactory.create(name="annual_report.pdf", file__data=b"full document text")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        contextualizing_index_manager.add_files(iterator)

        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance)
        assert embeddings.count() == 2
        for embedding in embeddings:
            assert "annual_report.pdf" in embedding.context
            assert embedding.contextualized_text.startswith(embedding.context)
            assert embedding.text in embedding.contextualized_text

    def test_no_context_when_contextualizer_none(self, local_index_instance):
        manager = LocalIndexManagerMock(api_key="api-123", embedding_model_name="embedding-model")
        file = FileFactory.create(name="annual_report.pdf", file__data=b"full document text")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        manager.add_files(iterator)

        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_index_instance)
        assert embeddings.count() == 2
        for embedding in embeddings:
            assert embedding.context == ""
            assert embedding.contextualized_text == embedding.text

    def test_embedded_input_includes_context(self, local_index_instance, contextualizing_index_manager):
        file = FileFactory.create(name="annual_report.pdf", file__data=b"full document text")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        with mock.patch.object(
            contextualizing_index_manager,
            "get_embedding_vector",
            wraps=contextualizing_index_manager.get_embedding_vector,
        ) as spy:
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            contextualizing_index_manager.add_files(iterator)

        assert spy.call_count > 0
        for call in spy.call_args_list:
            embedded_text = call.args[0]
            assert "annual_report.pdf" in embedded_text


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
        file = FileFactory.create(external_id="test_file_id_3", file__data=b"test content")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.FAILED
        collection_file.failure_reason = "ValueError: stale reason from the previous attempt"
        collection_file.save()

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED
        # A reason left by an earlier attempt describes an outcome that no longer holds.
        assert collection_file.failure_reason == ""

    def test_file_with_no_extractable_text_is_failed_without_uploading(self, remote_collection_index, index_manager):
        """The provider accepts a text-free file and reports nothing wrong.

        Source files are stored unparsed, so an image-only PDF reaches indexing. Linking it
        would leave it COMPLETED with nothing indexed, which no one can tell apart from a
        file that indexed cleanly.
        """
        file = FileFactory.create(file__data=b"   \n  ")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        with mock.patch.object(index_manager, "upload_file_to_remote") as upload:
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert "no text" in collection_file.failure_reason.lower()
        upload.assert_not_called()

    def test_unreadable_file_is_left_for_the_remote_index_to_judge(self, remote_collection_index, index_manager):
        """The provider indexes formats our readers do not, so a failed local read is not a
        verdict on the file -- only an empty successful read is."""
        file = FileFactory.create(file__data=b"content", external_id="test_file_id_4")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        with mock.patch.object(type(file), "read_content", side_effect=Exception("no reader for this type")):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            index_manager.add_files(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

    def test_add_files_with_file_upload_failures(self, remote_collection_index, index_manager):
        file = FileFactory.create(file__data=b"test content")
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
        file = FileFactory.create(external_id="test_file_id", file__data=b"test content")
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
        file1 = FileFactory.create(external_id="file-1")
        file2 = FileFactory.create(external_id="file-2")
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
        file1 = FileFactory.create(external_id="file-1")
        file2 = FileFactory.create(external_id="file-2")
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
        response = index_manager.chunk_file("This is test content.", chunk_size=2, chunk_overlap=0)
        assert response == ["This is", "test", "c", "on", "te", "nt", "."]

    def test_get_embedding_vector_document_calls_embed_documents(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_documents.return_value = [expected_vector]
            result = index_manager.get_embedding_vector("some text", input_type="document")

        mock_cls.return_value.embed_documents.assert_called_once_with(["some text"])
        mock_cls.return_value.embed_query.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_query_calls_embed_query(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_query.return_value = expected_vector
            result = index_manager.get_embedding_vector("some text", input_type="query")

        mock_cls.return_value.embed_query.assert_called_once_with("some text")
        mock_cls.return_value.embed_documents.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_passes_base_url_when_provided(self):
        manager = OpenAILocalIndexManager(
            api_key="api-123",
            embedding_model_name="embedding-model",
            openai_api_base="https://proxy.example.com/v1",
        )
        with mock.patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_query.return_value = [0.0] * settings.EMBEDDING_VECTOR_SIZE
            manager.get_embedding_vector("text", input_type="query")

        mock_cls.assert_called_once_with(
            api_key="api-123",
            model="embedding-model",
            dimensions=settings.EMBEDDING_VECTOR_SIZE,
            base_url="https://proxy.example.com/v1",
        )

    def test_get_embedding_vector_omits_base_url_when_unset(self, index_manager):
        with mock.patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_query.return_value = [0.0] * settings.EMBEDDING_VECTOR_SIZE
            index_manager.get_embedding_vector("text", input_type="query")

        mock_cls.assert_called_once_with(
            api_key="api-123",
            model="embedding-model",
            dimensions=settings.EMBEDDING_VECTOR_SIZE,
        )

    def test_get_embedding_vector_raises_on_unknown_input_type(self, index_manager):
        with mock.patch("langchain_openai.OpenAIEmbeddings"):
            with pytest.raises(ValueError, match="Unknown input_type"):
                index_manager.get_embedding_vector("some text", input_type="documents")  # type: ignore[arg-type]


class TestGoogleLocalIndexManager:
    @pytest.fixture()
    def index_manager(self):
        return GoogleLocalIndexManager(api_key="test-api-key", embedding_model_name="text-embedding-004")

    def test_get_embedding_vector_document_calls_embed_documents_with_dimensionality(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_google_genai.GoogleGenerativeAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_documents.return_value = [expected_vector]
            result = index_manager.get_embedding_vector("some text", input_type="document")

        mock_cls.assert_called_once_with(
            google_api_key="test-api-key",
            model="models/text-embedding-004",
        )
        mock_cls.return_value.embed_documents.assert_called_once_with(
            ["some text"],
            output_dimensionality=settings.EMBEDDING_VECTOR_SIZE,
            task_type="RETRIEVAL_DOCUMENT",
        )
        mock_cls.return_value.embed_query.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_query_calls_embed_query_with_dimensionality(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_google_genai.GoogleGenerativeAIEmbeddings") as mock_cls:
            mock_cls.return_value.embed_query.return_value = expected_vector
            result = index_manager.get_embedding_vector("some text", input_type="query")

        mock_cls.return_value.embed_query.assert_called_once_with(
            "some text",
            output_dimensionality=settings.EMBEDDING_VECTOR_SIZE,
            task_type="RETRIEVAL_QUERY",
        )
        mock_cls.return_value.embed_documents.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_raises_on_unknown_input_type(self, index_manager):
        with mock.patch("langchain_google_genai.GoogleGenerativeAIEmbeddings"):
            with pytest.raises(ValueError, match="Unknown input_type"):
                index_manager.get_embedding_vector("some text", input_type="documents")  # type: ignore[arg-type]


class TestVoyageAILocalIndexManager:
    @pytest.fixture()
    def index_manager(self):
        return VoyageAILocalIndexManager(api_key="test-api-key", embedding_model_name="voyage-4-large")

    def test_get_embedding_vector_document_calls_embed_documents(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_voyageai.VoyageAIEmbeddings") as mock_embeddings_cls:
            mock_embeddings_cls.return_value.embed_documents.return_value = [expected_vector]
            result = index_manager.get_embedding_vector("some text", input_type="document")

        mock_embeddings_cls.assert_called_once_with(
            voyage_api_key="test-api-key",
            model="voyage-4-large",
            output_dimension=settings.EMBEDDING_VECTOR_SIZE,
        )
        mock_embeddings_cls.return_value.embed_documents.assert_called_once_with(["some text"])
        mock_embeddings_cls.return_value.embed_query.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_query_calls_embed_query(self, index_manager):
        expected_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        with mock.patch("langchain_voyageai.VoyageAIEmbeddings") as mock_embeddings_cls:
            mock_embeddings_cls.return_value.embed_query.return_value = expected_vector
            result = index_manager.get_embedding_vector("some text", input_type="query")

        mock_embeddings_cls.return_value.embed_query.assert_called_once_with("some text")
        mock_embeddings_cls.return_value.embed_documents.assert_not_called()
        assert result == expected_vector

    def test_get_embedding_vector_raises_for_empty_content(self, index_manager):
        with pytest.raises(ValueError, match="Cannot embed empty string"):
            index_manager.get_embedding_vector("", input_type="document")

    def test_get_embedding_vector_raises_on_unknown_input_type(self, index_manager):
        with mock.patch("langchain_voyageai.VoyageAIEmbeddings"):
            with pytest.raises(ValueError, match="Unknown input_type"):
                index_manager.get_embedding_vector("some text", input_type="documents")  # type: ignore[arg-type]

    @pytest.mark.parametrize("input_type", ["document", "query"])
    def test_get_embedding_vector_rejects_contextual_models(self, input_type):
        index_manager = VoyageAILocalIndexManager(api_key="test-api-key", embedding_model_name="voyage-context-4")
        with mock.patch("langchain_voyageai.VoyageAIEmbeddings") as mock_embeddings_cls:
            with pytest.raises(ValueError, match="Contextual Voyage models are not supported"):
                index_manager.get_embedding_vector("some text", input_type=input_type)

        mock_embeddings_cls.assert_not_called()

    def test_embeddings_constructor_kwargs_are_accepted_by_the_installed_client(self):
        """Construct `VoyageAIEmbeddings` for real, unlike every other test in this class.

        The mocked tests would keep passing if a langchain-voyageai bump renamed or removed
        one of the kwargs we pass. `VoyageAIEmbeddings` sets `extra="forbid"` and constrains
        `output_dimension` to a literal set, so this fails on that drift. Nothing is embedded,
        so no network call is made.
        """
        from langchain_voyageai import VoyageAIEmbeddings  # noqa: PLC0415 - TID253: heavy lib, slow startup

        embeddings = VoyageAIEmbeddings(
            voyage_api_key="test-api-key",
            model="voyage-4-large",
            output_dimension=settings.EMBEDDING_VECTOR_SIZE,
        )

        assert embeddings.model == "voyage-4-large"
        assert embeddings.output_dimension == settings.EMBEDDING_VECTOR_SIZE
        # The contextual guard in `get_embedding_vector` reimplements this check; if the
        # upstream detection changes, the guard needs to change with it.
        assert VoyageAIEmbeddings(voyage_api_key="k", model="voyage-context-4")._is_context_model()
        assert not embeddings._is_context_model()


class TestOpenAILlmServiceLocalIndexManager:
    def test_get_local_index_manager_threads_openai_api_base(self):
        service = OpenAILlmService(
            openai_api_key="api-123",
            openai_api_base="https://proxy.example.com/v1",
        )
        manager = service.get_local_index_manager(embedding_model_name="text-embedding-3-small")

        assert isinstance(manager, OpenAILocalIndexManager)
        assert manager._openai_api_base == "https://proxy.example.com/v1"

    def test_get_local_index_manager_no_base_url(self):
        service = OpenAILlmService(openai_api_key="api-123")
        manager = service.get_local_index_manager(embedding_model_name="text-embedding-3-small")

        assert manager._openai_api_base is None
