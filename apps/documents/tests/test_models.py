from unittest import mock

import pytest
from django.conf import settings

from apps.files.models import FileChunkEmbedding
from apps.service_providers.llm_service.index_managers import LocalIndexManager, RemoteIndexManager
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


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

    def test_create_new_version_with_document_source(self):
        """Test basic version creation without vector store"""
        collection = CollectionFactory(is_index=False)
        document_source = DocumentSourceFactory(collection=collection)

        file1 = FileFactory()
        file2 = FileFactory()
        document_source.files.add(file1, file2, through_defaults={"collection": collection})

        new_collection_version = collection.create_new_version()
        document_source.refresh_from_db()
        assert document_source.versions.count() == 1
        new_doc_source_version = document_source.versions.first()

        assert new_doc_source_version.working_version == document_source
        assert new_doc_source_version.collection_id == new_collection_version.id

        assert new_doc_source_version.files.count() == 2
        assert new_collection_version.files.count() == 2
        for file_version in new_collection_version.files.all():
            assert file_version.working_version_id is not None

        # check that collection_files have the correct document_source version
        for collection_file in new_collection_version.collectionfile_set.all():
            assert collection_file.document_source_id == new_doc_source_version.id

    def test_recreate_issue(self):
        """
        This is a recreation of an issue where a file from the collection was versioned and linked to the
        document source's version that is linked to the collection.
        """
        collection = CollectionFactory(is_index=False)
        document_source = DocumentSourceFactory(collection=collection)

        file = FileFactory()
        collection.files.add(file)

        collection.create_new_version()
        document_source.refresh_from_db()
        new_doc_source_version = document_source.versions.first()
        assert new_doc_source_version.files.count() == 0

    @pytest.mark.usefixtures("remote_index_manager_mock")
    @mock.patch("apps.documents.tasks.index_collection_files")
    @mock.patch("apps.service_providers.models.LlmProvider.create_remote_index")
    def test_create_new_version_of_a_collection_index(self, create_remote_index, index_collection_files):
        """Ensure that a new vector store is created for the new version when one is created"""
        create_remote_index.return_value = "new-vs-123"

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
        create_remote_index.assert_called_once_with(name=new_version.index_name, file_ids=[])
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

    @mock.patch("apps.documents.tasks.delete_collection_task.delay")
    def test_archive_collection(self, delete_collection_task):
        """Test that a collection can be archived"""
        collection = CollectionFactory(openai_vector_store_id="vs-123")
        file = FileFactory(external_id="remote-file-123")
        collection.files.add(file)

        # Archive the collection
        collection.archive()

        # Check that the collection is archived
        assert collection.is_archived
        delete_collection_task.assert_called_once()

    def test_remove_remote_index(self, remote_index_manager_mock):
        """Test that the index can be removed"""
        collection = CollectionFactory(
            is_index=True, is_remote_index=True, openai_vector_store_id="vs-123", llm_provider=LlmProviderFactory()
        )

        # Invoke the remove_index method
        collection.remove_remote_index()

        # Check that the vector store ID is cleared and the index is removed
        assert collection.openai_vector_store_id == ""
        remote_index_manager_mock.delete_remote_index.assert_called_once()

    def test_get_index_manager_returns_correct_manager(self):
        """Remote indexes should return a remote index manager whereas local indexes should return a local one"""
        collection_remote = CollectionFactory(is_index=True, is_remote_index=True)
        collection_local = CollectionFactory(is_index=True, is_remote_index=False)

        assert isinstance(collection_remote.get_index_manager(), RemoteIndexManager)
        assert isinstance(collection_local.get_index_manager(), LocalIndexManager)

    @pytest.mark.parametrize(
        ("is_remote_index", "openai_id", "expect_remote_call"),
        [
            (True, "", True),
            (True, "vs-123", False),
            (False, "", False),
        ],
    )
    @mock.patch("apps.service_providers.models.LlmProvider.create_remote_index")
    def test_ensure_remote_index_created(self, create_remote_index, is_remote_index, openai_id, expect_remote_call):
        """Test creating vector store without file IDs"""
        collection = CollectionFactory(is_index=True, is_remote_index=is_remote_index, openai_vector_store_id=openai_id)
        create_remote_index.return_value = "new-vs-123"
        collection.ensure_remote_index_created()
        collection.refresh_from_db()

        if expect_remote_call:
            create_remote_index.assert_called()
            assert collection.openai_vector_store_id == "new-vs-123"
        else:
            create_remote_index.assert_not_called()
            assert collection.openai_vector_store_id == openai_id
