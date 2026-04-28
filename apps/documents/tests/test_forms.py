from unittest.mock import Mock

import pytest
from django.db.models import Q

from apps.documents.forms import CollectionForm, JSONCollectionDocumentSourceForm
from apps.documents.models import SourceType
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.service_provider_factories import EmbeddingProviderModelFactory, LlmProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
class TestCollectionForm:
    def test_validations(self, team):
        request = Mock(team=team)
        llm_provider = LlmProviderFactory.create(team=team)
        embedding_provider_model = EmbeddingProviderModelFactory.create(team=team)

        # Test creating a local index success
        data = {
            "name": "name",
            "is_index": True,
            "llm_provider": llm_provider.id,
            "embedding_provider_model": embedding_provider_model.id,
        }
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is True, f"Form should be valid but is not! Errors: {form.errors}"

        # Test creating a remote index success
        data = {"name": "name", "is_index": True, "is_remote_index": True, "llm_provider": llm_provider.id}
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is True, f"Form should be valid but is not! Errors: {form.errors}"

        # Edge cases
        # The user specified index fields, but decided to create a non-indexed collection
        data = {
            "name": "name",
            "is_index": False,
            "llm_provider": llm_provider.id,
            "embedding_provider_model": embedding_provider_model.id,
            "is_remote_index": True,
        }
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is True
        # Index fields should be cleared
        assert form.instance.is_remote_index is False
        assert form.instance.llm_provider is None
        assert form.instance.embedding_provider_model is None

        # The user specified local index fields, but decided to create a remote indexed collection
        data = {
            "name": "name",
            "is_index": True,
            "is_remote_index": True,
            "llm_provider": llm_provider.id,
            "embedding_provider_model": embedding_provider_model.id,
        }
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is True, f"Form should be valid but is not! Errors: {form.errors}"
        # embedding_provider_model should be cleared
        assert form.instance.embedding_provider_model is None

        # Indexed collection without llm_provider
        data = {"name": "name", "is_index": True, "llm_provider": None}
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is False, "Form should not be valid but it is!"

        # Local indexed collection without embedding_provider_model
        data = {"name": "name", "is_index": True, "llm_provider": llm_provider.id, "embedding_provider_model": None}
        form = CollectionForm(request=request, data=data)
        assert form.is_valid() is False, "Form should not be valid but it is!"

    def test_show_providers_with_embedding_models(self, team):
        """
        Only providers that have embedding models should be shown in the form.
        """
        request = Mock(team=team)
        openai_provider = LlmProviderFactory.create(team=team, type=LlmProviderTypes.openai)
        LlmProviderFactory.create(team=team, type=LlmProviderTypes.perplexity)

        # Global provider
        EmbeddingProviderModelFactory.create(type=LlmProviderTypes.openai)

        # Team specific providers
        EmbeddingProviderModelFactory.create(team=team, type=LlmProviderTypes.openai)

        team_b = TeamFactory.create()
        EmbeddingProviderModelFactory.create(team=team_b, type=LlmProviderTypes.openai)

        form = CollectionForm(request=request)
        assert form.fields["llm_provider"].queryset.count() == 1
        assert form.fields["llm_provider"].queryset.first() == openai_provider, (
            "LlmProvider queryset should only contain OpenAI provider"
        )

        assert form.fields["llm_provider"].queryset.exclude(Q(team_id=None) | Q(team_id=team.id)).exists() is False, (
            "Team specific models are not scoped to a team"
        )


@pytest.mark.django_db()
class TestJSONCollectionDocumentSourceForm:
    @pytest.fixture()
    def collection(self):
        return CollectionFactory.create()

    def test_valid_input_produces_config(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
            },
        )
        assert form.is_valid(), form.errors
        config = form.cleaned_data["config"]
        assert config.json_collection is not None
        assert str(config.json_collection.json_url) == "https://example.com/feed.json"
        assert config.json_collection.request_timeout == 30

    def test_invalid_url(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "not a url",
                "request_timeout": 30,
            },
        )
        assert not form.is_valid()
        assert "json_url" in form.errors

    def test_wrong_source_type_rejected(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.GITHUB,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
            },
        )
        assert not form.is_valid()
        assert "source_type" in form.errors

    def test_request_timeout_out_of_bounds(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 1,
            },
        )
        assert not form.is_valid()
        assert "request_timeout" in form.errors

    def test_no_auth_field_rendered(self, collection):
        form = JSONCollectionDocumentSourceForm(collection=collection)
        assert "auth_provider" not in form.fields
