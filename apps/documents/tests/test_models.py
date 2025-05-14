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

    @pytest.mark.django_db()
    @mock.patch("apps.documents.tasks.index_collection_files")
    def test_create_new_version_of_a_collection_index(self, index_collection_files):
        """Ensure that a new vector store is created for the new version when one is created"""
        index_manager_mock = mock.Mock()
        index_manager_mock.create_vector_store.return_value = "new-vs-123"

        with mock.patch("apps.service_providers.models.LlmProvider.get_index_manager") as get_index_manager:
            get_index_manager.return_value = index_manager_mock
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
            index_collection_files.assert_called_once_with(new_version.id, all_files=True)
