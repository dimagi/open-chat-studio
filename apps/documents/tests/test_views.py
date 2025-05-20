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

    @mock.patch("apps.documents.tasks.migrate_vector_stores.delay")
    def test_update_collection_with_llm_provider_change(self, migrate_mock, index_manager_mock, collection, client):
        new_llm_provider = LlmProviderFactory(team=collection.team)
        new_vector_store_id = "new-store-123"
        index_manager_mock.create_vector_store.return_value = new_vector_store_id

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
    def setup_collection(self, is_index: bool) -> Collection:
        team = TeamWithUsersFactory()
        file = FileFactory(team=team, external_id="remote-file-123")
        collection = CollectionFactory(
            name="Tester",
            team=team,
            is_index=is_index,
            llm_provider=LlmProviderFactory(team=team),
            openai_vector_store_id="store-123",
        )
        collection.files.add(file)
        return collection

    @pytest.mark.parametrize("is_index", [False])
    def test_user_cannot_delete_a_collection_in_use(self, is_index, index_manager_mock, client, experiment):
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
        index_manager_mock.create_vector_store.return_value = "v-321"
        collection.create_new_version()
        node.params = {}
        node.save()

        response = client.delete(url)
        assert response.status_code == 400

    @pytest.mark.usefixtures("index_manager_mock")
    @pytest.mark.parametrize("is_index", [True, False])
    def test_collection_is_archived(self, is_index, client):
        collection = self.setup_collection(is_index=is_index)
        client.force_login(collection.team.members.first())

        url = reverse("documents:collection_delete", args=[collection.team.slug, collection.id])
        response = client.delete(url)

        assert response.status_code == 200
        collection.refresh_from_db()
        assert collection.is_archived
