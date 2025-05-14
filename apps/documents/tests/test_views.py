from unittest import mock

import pytest
from django.urls import reverse

from apps.documents.models import Collection, CollectionFile, FileStatus
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
        return CollectionFactory(name="Tester", team=team, is_index=True, llm_provider=LlmProviderFactory(team=team))

    @mock.patch("apps.service_providers.models.LlmProvider.get_index_manager")
    @mock.patch("apps.documents.tasks.migrate_vector_stores.delay")
    def test_update_collection_with_llm_provider_change(self, migrate_mock, get_index_manager, collection, client):
        new_llm_provider = LlmProviderFactory(team=collection.team)
        new_vector_store_id = "new-store-123"

        manager_instance = mock.Mock()
        manager_instance.create_vector_store.return_value = new_vector_store_id
        get_index_manager.return_value = manager_instance

        client.force_login(collection.team.members.first())
        url = reverse("documents:collection_edit", args=[collection.team.slug, collection.id])

        response = client.post(
            url,
            {
                "name": collection.name,
                "is_index": True,
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

    @mock.patch("apps.documents.views.migrate_vector_stores.delay")
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
    @pytest.fixture()
    def collection(self):
        team = TeamWithUsersFactory()
        file = FileFactory(team=team)
        collection = CollectionFactory(
            name="Tester",
            team=team,
            is_index=True,
            llm_provider=LlmProviderFactory(team=team),
            openai_vector_store_id="store-123",
        )
        collection.files.add(file)
        return collection

    @mock.patch("apps.documents.models.Collection.remove_index")
    def test_index_and_files_removed_on_delete(self, remove_index, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])

        response = client.delete(url)

        assert response.status_code == 200
        with pytest.raises(Collection.DoesNotExist):
            collection.refresh_from_db()

        remove_index.assert_called()

    def test_user_cannot_delete_a_collection_in_use(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])

        NodeFactory(pipeline=PipelineFactory(), type="LlmNode", params={"collection_index_id": collection.id})

        response = client.delete(url)
        assert response.status_code == 400
        collection.refresh_from_db()
        assert collection.name == "Tester"  # Collection should not be deleted

    @mock.patch("apps.documents.models.Collection.remove_index")
    def test_versioned_collection_is_archived(self, remove_index, collection, client):
        """A versioned collection should be archived and its index and files removed from the provider"""
        client.force_login(collection.team.members.first())

        # Create a version of the collection
        version = CollectionFactory(working_version=collection, openai_vector_store_id="new-id")

        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])
        response = client.delete(url)

        assert response.status_code == 200
        collection.refresh_from_db()
        assert collection.is_archived  # Collection should be archived, not deleted
        version.refresh_from_db()  # Version should still exist

        remove_index.assert_called_once()
