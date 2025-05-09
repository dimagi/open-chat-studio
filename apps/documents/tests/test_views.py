from unittest import mock

import pytest
from django.urls import reverse

from apps.documents.models import CollectionFile, FileStatus
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
class TestEditCollection:
    @pytest.fixture()
    def collection(self):
        team = TeamWithUsersFactory()
        return CollectionFactory(name="Tester", team=team, is_index=True, llm_provider=LlmProviderFactory(team=team))

    @mock.patch("apps.documents.views.VectorStoreManager")
    @mock.patch("apps.documents.tasks.migrate_vector_stores.delay")
    def test_update_collection_with_llm_provider_change(self, migrate_mock, manager_mock, collection, client):
        new_llm_provider = LlmProviderFactory(team=collection.team)
        new_vector_store_id = "new-store-123"

        manager_instance = mock.Mock()
        manager_instance.create_vector_store.return_value = new_vector_store_id
        manager_mock.from_llm_provider.return_value = manager_instance

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

    def test_update_collection_without_llm_provider_change(self, collection, client):
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
