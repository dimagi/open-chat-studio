from unittest import mock

import pytest
from django.urls import reverse

from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
class TestEditCollection:
    @pytest.fixture()
    def collection(self):
        team = TeamWithUsersFactory()
        return CollectionFactory(
            name="Tester", team=team, is_index=True, is_remote_index=True, llm_provider=LlmProviderFactory(team=team)
        )

    @pytest.mark.usefixtures("remote_index_manager_mock")
    @mock.patch("apps.documents.tasks.migrate_vector_stores.delay")
    @mock.patch("apps.service_providers.models.LlmProvider.create_remote_index")
    def test_update_collection_with_llm_provider_change(self, create_remote_index, migrate_mock, collection, client):
        new_llm_provider = LlmProviderFactory(team=collection.team)
        new_vector_store_id = "new-store-123"
        create_remote_index.return_value = new_vector_store_id

        client.force_login(collection.team.members.first())
        url = reverse("documents:collection_edit", args=[collection.team.slug, collection.id])

        response = client.post(
            url,
            {
                "name": collection.name,
                "is_index": True,
                "is_remote_index": True,
                "llm_provider": new_llm_provider.id,
            },
        )

        assert response.status_code == 302
        collection.refresh_from_db()
        assert collection.llm_provider == new_llm_provider
        assert collection.openai_vector_store_id == new_vector_store_id

        # Verify that files are marked for reprocessing
        CollectionFile.objects.filter(collection=collection).update(status=FileStatus.PENDING)

        # Verify migration task was called
        migrate_mock.assert_called_once_with(
            collection_id=collection.id,
            from_vector_store_id=mock.ANY,
            from_llm_provider_id=mock.ANY,
        )

    @mock.patch("apps.documents.views.tasks.migrate_vector_stores.delay")
    def test_update_collection_without_llm_provider_change(self, migrate_vector_stores_task, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:collection_edit", args=[collection.team.slug, collection.id])
        new_name = "Updated Collection Name"

        response = client.post(
            url,
            {
                "name": new_name,
                "is_index": True,
                "llm_provider": collection.llm_provider.id,
            },
        )

        assert response.status_code == 302
        collection.refresh_from_db()
        assert collection.name == new_name
        migrate_vector_stores_task.assert_not_called()


@pytest.mark.django_db()
class TestDeleteCollection:
    def setup_collection(self, is_index: bool) -> Collection:
        team = TeamWithUsersFactory()
        file = FileFactory(team=team, external_id="remote-file-123")
        collection = CollectionFactory(
            name="Tester",
            team=team,
            is_index=is_index,
            is_remote_index=is_index,
            llm_provider=LlmProviderFactory(team=team),
            openai_vector_store_id="store-123",
        )
        collection.files.add(file)
        return collection

    @pytest.mark.usefixtures("remote_index_manager_mock")
    @pytest.mark.parametrize("is_index", [True, False])
    @mock.patch("apps.service_providers.models.LlmProvider.create_remote_index")
    def test_user_cannot_delete_a_collection_in_use(self, create_remote_index, is_index, client, experiment):
        """
        The user should not be able to delete a collection if it is being used by a pipeline.
        There are two cases where this can happen:
        1. The collection is being used in a pipeline
        2. The collection is being used by a pipeline version, which is being used by some published experiment
        """
        experiment.pipeline = PipelineFactory()
        experiment.save()

        collection = self.setup_collection(is_index=is_index)
        client.force_login(collection.team.members.first())
        node = NodeFactory(pipeline=experiment.pipeline, type="LlmNode", params={"collection_index_id": collection.id})
        experiment.create_new_version()

        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])
        # Case 1 - The pipeline is using the collection
        response = client.delete(url)
        assert response.status_code == 400

        # Case 2 - Remove the collection from the node so that only a pipeline version is using it
        create_remote_index.return_value = "v-321"
        collection.create_new_version()
        node.params = {}
        node.save()

        response = client.delete(url)
        assert response.status_code == 400

    @pytest.mark.usefixtures("remote_index_manager_mock")
    @pytest.mark.parametrize("is_index", [True, False])
    def test_collection_is_archived(self, is_index, client):
        collection = self.setup_collection(is_index=is_index)
        client.force_login(collection.team.members.first())

        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])
        response = client.delete(url)

        assert response.status_code == 200
        collection.refresh_from_db()
        assert collection.is_archived


@pytest.mark.django_db()
class TestDeleteCollectionFile:
    @pytest.fixture()
    def team_with_user(self):
        return TeamWithUsersFactory()

    def test_delete_file_from_non_indexed_collection(self, team_with_user, client):
        """Test deleting a file from a non-indexed collection when file is not used elsewhere."""
        collection = CollectionFactory(team=team_with_user, is_index=False)
        file = FileFactory(team=team_with_user, external_id="file-123", external_source="openai")
        CollectionFile.objects.create(collection=collection, file=file)

        client.force_login(team_with_user.members.first())

        # Mock the file deletion since it's not used elsewhere
        with mock.patch.object(File, "delete_or_archive") as mock_delete_archive:
            url = reverse("documents:delete_collection_file", args=[team_with_user.slug, collection.id, file.id])
            client.post(url)

            # Verify collection file relationship is deleted
            assert not CollectionFile.objects.filter(collection=collection, file=file).exists()

            # Verify file is deleted/archived since it's not used elsewhere
            mock_delete_archive.assert_called_once()

    @pytest.mark.parametrize("is_remote_index", [True, False])
    def test_delete_file_from_indexed_collection(
        self, is_remote_index, team_with_user, client, remote_index_manager_mock, local_index_manager_mock
    ):
        """Test deleting a file from an indexed collection when file is not used by an assistant."""
        llm_provider = LlmProviderFactory(team=team_with_user)
        collection = CollectionFactory(
            team=team_with_user,
            is_index=True,
            is_remote_index=is_remote_index,
            llm_provider=llm_provider,
            openai_vector_store_id="vs-123",
        )
        file = FileFactory(team=team_with_user, external_id="file-123", external_source="openai")
        CollectionFile.objects.create(collection=collection, file=file)

        client.force_login(team_with_user.members.first())

        # Mock the file as not being used elsewhere
        with mock.patch.object(File, "delete_or_archive") as mock_delete_archive:
            url = reverse("documents:delete_collection_file", args=[team_with_user.slug, collection.id, file.id])
            client.post(url)

            # Verify collection file relationship is deleted
            assert CollectionFile.objects.filter(collection=collection, file=file).exists() is False

            if is_remote_index:
                # Verify OpenAI file deletion was called for indexed collection
                remote_index_manager_mock.delete_files.assert_called_once()
            else:
                # Verify local index file deletion was called for local indexed collection
                local_index_manager_mock.delete_files.assert_called_once()

            # Verify file is deleted/archived since it's not used elsewhere
            mock_delete_archive.assert_called_once()

    @pytest.mark.parametrize("is_remote_index", [True, False])
    def test_delete_shared_file_from_remote_indexed_collection(
        self, is_remote_index, team_with_user, client, remote_index_manager_mock, local_index_manager_mock
    ):
        """Test deleting a file from an indexed collection when file is also used by another object."""
        # Setup: Create indexed collection with file
        llm_provider = LlmProviderFactory(team=team_with_user)
        collection = CollectionFactory(
            team=team_with_user,
            is_index=True,
            is_remote_index=is_remote_index,
            llm_provider=llm_provider,
            openai_vector_store_id="vs-123",
        )
        file = FileFactory(team=team_with_user, external_id="file-123", external_source="openai")
        CollectionFile.objects.create(collection=collection, file=file)

        # Login user
        client.force_login(team_with_user.members.first())

        # Mock the file as being used elsewhere (by assistant)
        with (
            mock.patch.object(File, "is_used", return_value=True),
            mock.patch.object(File, "delete_or_archive") as mock_delete_archive,
        ):
            url = reverse("documents:delete_collection_file", args=[team_with_user.slug, collection.id, file.id])
            client.post(url)

            # Verify collection file relationship is deleted
            assert CollectionFile.objects.filter(collection=collection, file=file).exists() is False

            # Verify file is NOT deleted/archived since it's used by assistant
            mock_delete_archive.assert_not_called()

            # Verify OpenAI file deletion was NOT called since file is still used
            remote_index_manager_mock.delete_files.assert_not_called()

            if is_remote_index:
                remote_index_manager_mock.delete_files_from_index.assert_called()
            else:
                local_index_manager_mock.delete_files.assert_not_called()

            # Verify file still exists and is still linked to assistant
            file.refresh_from_db()
            assert file.is_archived is False
