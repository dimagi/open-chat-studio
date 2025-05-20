from unittest import mock

import pytest

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
    def test_create_new_version_of_a_collection_index(self, index_collection_files, index_manager_mock):
        """Ensure that a new vector store is created for the new version when one is created"""
        index_manager_mock.create_vector_store.return_value = "new-vs-123"

        collection = CollectionFactory(
            name="Test Collection",
            is_index=True,
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
        index_manager_mock.create_vector_store.assert_called_once_with(
            name=f"{new_version.index_name} v{new_version.version_number}"
        )
        index_collection_files.assert_called_once_with(new_version.id, re_upload_all=True)

    @pytest.mark.parametrize("is_index", [True, False])
    @mock.patch("apps.documents.models.Collection._remove_index")
    def test_archive_collection(self, _remove_index, is_index):
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
            _remove_index.assert_called_once()
        else:
            _remove_index.assert_not_called()

    def test_remove_index(self, index_manager_mock):
        """Test that the index can be removed"""
        collection = CollectionFactory(
            is_index=True, openai_vector_store_id="vs-123", llm_provider=LlmProviderFactory()
        )
        file = FileFactory(external_id="remote-file-123")
        collection.files.add(file)

        # Invoke the remove_index method
        collection._remove_index()

        # Check that the vector store ID is cleared and the index is removed
        assert collection.openai_vector_store_id == ""
        file.refresh_from_db()
        index_manager_mock.delete_vector_store.assert_called_once_with("vs-123", fail_silently=True)
        index_manager_mock.delete_files.assert_called_once()
