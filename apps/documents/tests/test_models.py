from unittest import mock

import pytest
from django.conf import settings

from apps.assistants.models import ToolResources
from apps.documents.exceptions import FileUploadError, IndexConfigurationException
from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import FileChunkEmbedding
from apps.service_providers.exceptions import UnableToLinkFileException
from apps.service_providers.llm_service.index_managers import LocalIndexManager, RemoteIndexManager
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.mark.django_db()
class TestNode:
    def test_create_new_version(self):
        collection = CollectionFactory()
        file = FileFactory()
        collection.files.add(file)

        collection_v = collection.create_new_version()

        assert file.versions.count() == 1

        assert collection_v.files.first() == file.versions.first()


@pytest.fixture()
def remote_collection_index(db):
    return CollectionFactory(is_index=True, is_remote_index=True)


@pytest.fixture()
def local_collection_index(db):
    return CollectionFactory(is_index=True, is_remote_index=False)


@pytest.mark.django_db()
class TestCollection:
    def test_create_new_version(self):
        """Test basic version creation without vector store"""
        collection = CollectionFactory(is_index=False)
        file1 = FileFactory()
        file2 = FileFactory()
        collection.files.add(file1, file2)

        # Create new version
        new_version = collection.create_new_version()
        collection.refresh_from_db()

        # Check version numbers
        assert collection.version_number == 2
        assert new_version.version_number == 1
        assert new_version.working_version == collection

        # Check files were versioned
        assert new_version.files.count() == 2
        for file_version in new_version.files.all():
            assert file_version.external_id == ""
            assert file_version.working_version in [file1, file2]

        # Vector store ID should be None for non-indexed collections
        assert new_version.openai_vector_store_id == ""

    @mock.patch("apps.documents.tasks.index_collection_files")
    def test_create_new_version_of_a_collection_index(self, index_collection_files, remote_index_manager_mock):
        """Ensure that a new vector store is created for the new version when one is created"""
        remote_index_manager_mock.create_remote_index.return_value = "new-vs-123"

        collection = CollectionFactory(
            name="Test Collection",
            is_index=True,
            is_remote_index=True,
            openai_vector_store_id="old-vs-123",
            llm_provider=LlmProviderFactory(),
        )
        file = FileFactory()
        collection.files.add(file)

        # Create new version
        new_version = collection.create_new_version()
        collection.refresh_from_db()

        # Check basic versioning worked
        assert collection.version_number == 2
        assert new_version.version_number == 1
        assert new_version.working_version == collection

        # Check vector store handling
        assert new_version.openai_vector_store_id == "new-vs-123"
        assert collection.openai_vector_store_id == "old-vs-123"

        # Verify vector store was created and files were indexed
        remote_index_manager_mock.create_remote_index.assert_called_once_with(
            name=f"{new_version.index_name} v{new_version.version_number}"
        )
        index_collection_files.assert_called()

    def test_create_new_version_of_local_collection_index(self):
        """Ensure that file chunk embeddings are versioned when creating a new version of a local index"""
        collection = CollectionFactory(
            name="Test Local Collection",
            is_index=True,
            is_remote_index=False,
            llm_provider=LlmProviderFactory(),
        )
        file = FileFactory()
        collection.files.add(file)

        # Create some file chunk embeddings for the original collection
        original_embedding_1 = FileChunkEmbedding.objects.create(
            team_id=collection.team_id,
            file=file,
            collection=collection,
            chunk_number=0,
            text="First chunk of text",
            embedding=[0.1] * settings.EMBEDDING_VECTOR_SIZE,
            page_number=1,
        )
        original_embedding_2 = FileChunkEmbedding.objects.create(
            team_id=collection.team_id,
            file=file,
            collection=collection,
            chunk_number=1,
            text="Second chunk of text",
            embedding=[0.2] * settings.EMBEDDING_VECTOR_SIZE,
            page_number=1,
        )

        # Create new version
        new_version = collection.create_new_version()
        collection.refresh_from_db()

        # Check that file chunk embeddings were versioned
        new_embeddings = FileChunkEmbedding.objects.filter(collection=new_version)
        assert new_embeddings.count() == 2

        # Verify the versioned embeddings are correctly linked
        file_version = new_version.files.first()
        for new_embedding in new_embeddings:
            assert new_embedding.file == file_version
            assert new_embedding.collection == new_version
            assert new_embedding.working_version in [original_embedding_1, original_embedding_2]

        # Verify original embeddings still exist and are unchanged
        assert FileChunkEmbedding.objects.filter(collection=collection).count() == 2

    @pytest.mark.parametrize("is_index", [True, False])
    @mock.patch("apps.documents.models.Collection._remove_remote_index")
    def test_archive_collection(self, _remove_remote_index, is_index):
        """Test that a collection can be archived"""
        provider = LlmProviderFactory() if is_index else None
        collection = CollectionFactory(is_index=is_index, openai_vector_store_id="vs-123", llm_provider=provider)
        file = FileFactory(external_id="remote-file-123")
        collection.files.add(file)

        # Archive the collection
        collection.archive()

        # Check that the collection and files are archived and files cleared
        file.refresh_from_db()
        assert collection.is_archived

        for file in collection.files.all():
            assert file.is_archived

        if is_index:
            _remove_remote_index.assert_called_once()
        else:
            _remove_remote_index.assert_not_called()

    @mock.patch("apps.documents.models.Collection._remove_remote_index")
    def test_archive_collection_does_not_archive_files_in_use(self, _remove_index):
        """Test that a collection can be archived"""
        collection = CollectionFactory()
        file = FileFactory(external_id="remote-file-123")
        collection.files.add(file)
        resource = ToolResources.objects.create(assistant=OpenAiAssistantFactory())
        resource.files.add(file)

        # Archive the collection
        collection.archive()

        # Check that only the collection is archived, not the file
        assert collection.is_archived
        file.refresh_from_db()
        assert file.is_archived is False

    def test_remove_remote_index(self, remote_index_manager_mock):
        """Test that the index can be removed"""
        collection = CollectionFactory(
            is_index=True, is_remote_index=True, openai_vector_store_id="vs-123", llm_provider=LlmProviderFactory()
        )
        file = FileFactory(external_id="remote-file-123")
        collection.files.add(file)

        # Invoke the remove_index method
        collection._remove_remote_index([file])

        # Check that the vector store ID is cleared and the index is removed
        assert collection.openai_vector_store_id == ""
        file.refresh_from_db()
        remote_index_manager_mock.delete_vector_store.assert_called_once_with(fail_silently=True)
        remote_index_manager_mock.delete_files.assert_called_once()

    def test_get_index_manager_returns_correct_manager(self):
        """Remote indexes should return a remote index manager whereas local indexes should return a local one"""
        collection_remote = CollectionFactory(is_index=True, is_remote_index=True)
        collection_local = CollectionFactory(is_index=True, is_remote_index=False)

        assert isinstance(collection_remote.get_index_manager(), RemoteIndexManager)
        assert isinstance(collection_local.get_index_manager(), LocalIndexManager)

    def test_handle_remote_indexing_success(self, remote_collection_index, remote_index_manager_mock):
        file = FileFactory(external_id="test_file_id_3")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock successful upload and linking
        remote_index_manager_mock.ensure_remote_file_exists.side_effect = None
        remote_index_manager_mock.link_files_to_remote_index.side_effect = None

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        remote_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

    def test_handle_remote_indexing_with_file_upload_failures(self, remote_collection_index, remote_index_manager_mock):
        file = FileFactory()
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock ensure_remote_file_exists to raise FileUploadError
        remote_index_manager_mock.ensure_remote_file_exists.side_effect = FileUploadError("Upload failed")

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        remote_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        remote_index_manager_mock.ensure_remote_file_exists.assert_called_once()
        # link_files_to_remote_index should be called with empty list when all uploads fail
        remote_index_manager_mock.link_files_to_remote_index.assert_called_once_with(
            file_ids=[], chunk_size=None, chunk_overlap=None
        )

    def test_handle_remote_indexing_with_linking_failures(self, remote_collection_index, remote_index_manager_mock):
        file = FileFactory(external_id="test_file_id")
        remote_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=remote_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock successful upload but failed linking
        remote_index_manager_mock.ensure_remote_file_exists.side_effect = None
        remote_index_manager_mock.link_files_to_remote_index.side_effect = UnableToLinkFileException("Link failed")

        iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
        remote_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        remote_index_manager_mock.link_files_to_remote_index.assert_called_once()

    def test_handle_local_indexing_success(self, local_collection_index, local_index_manager_mock):
        file = FileFactory()
        local_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_collection_index, file=file)

        # Mock the index manager and file content reading
        local_index_manager_mock.chunk_file.return_value = ["test", "content"]
        local_index_manager_mock.get_embedding_vector.return_value = [0.1] * settings.EMBEDDING_VECTOR_SIZE

        with mock.patch.object(file, "read_content", return_value="test content"):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            local_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        # Verify that embeddings were created
        embeddings = FileChunkEmbedding.objects.filter(file=file, collection=local_collection_index)
        assert embeddings.count() == 2
        assert embeddings.first().text == "test"
        assert embeddings.last().text == "content"

    def test_handle_local_indexing_fails(self, local_collection_index):
        """If anything goes wrong during local indexing, the file should be marked as failed"""
        file = FileFactory()
        local_collection_index.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_collection_index, file=file)
        collection_file.status = FileStatus.PENDING
        collection_file.save()

        # Mock file.read_content to raise an exception
        with mock.patch.object(file, "read_content", side_effect=Exception("Read failed")):
            iterator = CollectionFile.objects.filter(id=collection_file.id).iterator(1)
            local_collection_index.add_files_to_index(iterator)

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED

    def test_get_query_vector(self, local_index_manager_mock):
        """Test that get_query_vector raises an exception for remote indexes"""
        collection = CollectionFactory(is_index=True, is_remote_index=False)
        collection.get_query_vector("test query")
        local_index_manager_mock.get_embedding_vector.assert_called_once_with("test query")

    def test_get_query_vector_with_missing_embedding_model(self):
        """Test that get_query_vector raises an exception for remote indexes"""
        collection = CollectionFactory(is_index=True, is_remote_index=False, embedding_provider_model=None)
        with pytest.raises(IndexConfigurationException):
            collection.get_query_vector("test query")
