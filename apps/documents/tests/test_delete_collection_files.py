from unittest.mock import Mock, patch

import pytest

from apps.documents.models import CollectionFile
from apps.documents.utils import bulk_delete_collection_files
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
class TestBulkDeleteCollectionFiles:
    """Test suite for bulk_delete_collection_files function."""

    def test_delete_files_not_used_elsewhere(self, team_with_users):
        """Test deleting files that are not used in other collections."""
        collection = CollectionFactory(team=team_with_users, is_index=False)
        file1 = FileFactory(team=team_with_users)
        file2 = FileFactory(team=team_with_users)

        collection.files.add(file1, file2)
        collection_files = list(CollectionFile.objects.filter(collection=collection))

        # Mock that files are not used elsewhere
        with patch("apps.documents.utils.get_related_m2m_objects") as mock_get_related:
            mock_get_related.return_value = []

            bulk_delete_collection_files(collection, collection_files)

            assert CollectionFile.objects.filter(collection=collection).count() == 0
            assert File.objects.filter(id__in=[f.id for f in [file1, file2]]).count() == 0

    def test_delete_files_used_elsewhere(self, team_with_users):
        """Test deleting files that are used in other collections."""
        collection1 = CollectionFactory(team=team_with_users, is_index=True)
        file1 = FileFactory(team=team_with_users)
        file2 = FileFactory(team=team_with_users)

        collection1.files.add(file1, file2)

        collection_files = list(CollectionFile.objects.filter(collection=collection1))

        mock_index_manager = Mock()
        with (
            patch.object(collection1, "get_index_manager", return_value=mock_index_manager),
            patch("apps.documents.utils.get_related_m2m_objects") as mock_get_related,
        ):
            # mock that file1 is used elsewhere bot not file2
            mock_get_related.return_value = {file1: []}

            bulk_delete_collection_files(collection1, collection_files)

            assert CollectionFile.objects.filter(collection=collection1).count() == 0
            assert not File.objects.filter(id=file2.id).exists()  # file2 deleted

            # ensure file1 are still present and not archived
            file1.refresh_from_db()
            assert not file1.is_archived

            mock_index_manager.delete_files_from_index.assert_called_once_with(files=[file1])
            mock_index_manager.delete_files.assert_called_once_with(files=[file2])

    def test_delete_files_not_used_elsewhere_with_versions(self, team_with_users):
        """Test deleting files that are not used in other collections."""
        collection = CollectionFactory(team=team_with_users, is_index=False)
        file1 = FileFactory(team=team_with_users)
        file2 = FileFactory(team=team_with_users)

        collection.files.add(file1, file2)
        collection_files = list(CollectionFile.objects.filter(collection=collection))

        # create a new version of file1
        file1.create_new_version()

        # Mock that files are not used elsewhere
        with patch("apps.documents.utils.get_related_m2m_objects") as mock_get_related:
            mock_get_related.return_value = []

            bulk_delete_collection_files(collection, collection_files)

            assert CollectionFile.objects.filter(collection=collection).count() == 0
            assert not File.objects.filter(id=file2.id).exists()  # file2 deleted

            file1.refresh_from_db()
            assert file1.is_archived

    def test_delete_files_full(self, team_with_users):
        collection = CollectionFactory(team=team_with_users, is_index=False)
        file1 = FileFactory(team=team_with_users)

        collection.files.add(file1)
        collection_files = list(CollectionFile.objects.filter(collection=collection))

        bulk_delete_collection_files(collection, collection_files)

        assert CollectionFile.objects.filter(collection=collection).count() == 0
        assert not File.objects.filter(id=file1.id).exists()
