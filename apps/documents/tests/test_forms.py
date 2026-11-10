from unittest.mock import Mock

import pytest
from django.db.models import Q

from apps.documents.datamodels import (
    DocumentSourceConfig,
    GoogleDocsSourceConfig,
    JSONCollectionSourceConfig,
)
from apps.documents.forms import CollectionForm, GoogleDocsDocumentSourceForm, JSONCollectionDocumentSourceForm
from apps.documents.models import SourceType
from apps.service_providers.models import AuthProviderType, LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
)
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

    def test_auth_field_is_optional(self, collection):
        form = JSONCollectionDocumentSourceForm(collection=collection)
        assert "auth_provider" in form.fields
        assert form.fields["auth_provider"].required is False

    def test_form_valid_without_auth_provider(self, collection):
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
        assert form.cleaned_data["auth_provider"] is None

    def test_auth_provider_queryset_excludes_commcare(self, collection):
        bearer = AuthProviderFactory.create(team=collection.team, type=AuthProviderType.bearer, config={"token": "t"})
        basic = AuthProviderFactory.create(
            team=collection.team, type=AuthProviderType.basic, config={"username": "u", "password": "p"}
        )
        api_key = AuthProviderFactory.create(
            team=collection.team, type=AuthProviderType.api_key, config={"key": "k", "value": "v"}
        )
        commcare = AuthProviderFactory.create(team=collection.team, type=AuthProviderType.commcare)

        form = JSONCollectionDocumentSourceForm(collection=collection)
        ids = list(form.fields["auth_provider"].queryset.values_list("id", flat=True))
        assert set(ids) == {bearer.id, basic.id, api_key.id}
        assert commcare.id not in ids

    def test_form_valid_with_bearer_auth_provider(self, collection):
        provider = AuthProviderFactory.create(
            team=collection.team, type=AuthProviderType.bearer, config={"token": "secret"}
        )
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
                "auth_provider": provider.id,
            },
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["auth_provider"] == provider

    def test_metadata_filter_widget_renders(self, collection):
        """The custom widget + form template render without error and emit the editor markup."""
        instance = DocumentSourceFactory.create(
            collection=collection,
            source_type=SourceType.JSON_COLLECTION,
            config=DocumentSourceConfig(
                json_collection=JSONCollectionSourceConfig(
                    json_url="https://example.com/feed.json",
                    metadata_filters=[{"field": "status", "operator": "eq", "value": "published"}],
                )
            ),
        )
        form = JSONCollectionDocumentSourceForm(collection=collection, instance=instance, initial={})
        html = str(form["metadata_filters"])
        assert "metadataFilterEditor" in html
        assert "published" in html

    def test_metadata_filters_and_file_types_parsed_into_config(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
                "metadata_filters": '[{"field": "status", "operator": "eq", "value": "published"}]',
                "unsupported_file_types": "mp3, MP4 , wav",
            },
        )
        assert form.is_valid(), form.errors
        config = form.cleaned_data["config"].json_collection
        assert len(config.metadata_filters) == 1
        assert config.metadata_filters[0].field == "status"
        assert config.metadata_filters[0].operator == "eq"
        assert config.metadata_filters[0].value == "published"
        assert config.unsupported_file_types == ["mp3", "MP4", "wav"]

    def test_invalid_metadata_filter_operator_rejected(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
                "metadata_filters": '[{"field": "status", "operator": "bogus", "value": "x"}]',
            },
        )
        assert not form.is_valid()

    def test_existing_config_round_trips_to_initial(self, collection):
        instance = DocumentSourceFactory.create(
            collection=collection,
            source_type=SourceType.JSON_COLLECTION,
            config=DocumentSourceConfig(
                json_collection=JSONCollectionSourceConfig(
                    json_url="https://example.com/feed.json",
                    metadata_filters=[{"field": "status", "operator": "eq", "value": "published"}],
                    unsupported_file_types=["mp3", "wav"],
                )
            ),
        )
        form = JSONCollectionDocumentSourceForm(collection=collection, instance=instance, initial={})
        assert form.initial["unsupported_file_types"] == "mp3, wav"
        assert form.initial["metadata_filters"] == [{"field": "status", "operator": "eq", "value": "published"}]


@pytest.mark.django_db()
class TestGoogleDocsDocumentSourceForm:
    @pytest.fixture()
    def collection(self):
        return CollectionFactory.create()

    @pytest.fixture()
    def oauth_provider(self, collection):
        return AuthProviderFactory.create(
            team=collection.team,
            type=AuthProviderType.oauth_authorization_code,
            config={
                "client_id": "client",
                "client_secret": "secret",
                "authorize_url": "https://accounts.google.test/o/oauth2/auth",
                "token_url": "https://oauth2.google.test/token",
                "scope": "https://www.googleapis.com/auth/documents.readonly",
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )

    def test_valid_form_saves_google_docs_config(self, collection, oauth_provider):
        url = "https://docs.google.com/document/d/doc_123/edit"
        form = GoogleDocsDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.GOOGLE_DOCS,
                "auto_sync_enabled": False,
                "auth_provider": oauth_provider.id,
                "document_url": url,
            },
        )

        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.source_type == SourceType.GOOGLE_DOCS
        assert saved.auth_provider == oauth_provider
        assert saved.config.google_docs.document_url == url
        assert saved.source_config == saved.config.google_docs

    @pytest.mark.parametrize(
        "document_url",
        [
            "http://docs.google.com/document/d/doc_123/edit",
            "https://example.com/document/d/doc_123/edit",
            "https://docs.google.com/spreadsheets/d/doc_123/edit",
            "https://docs.google.com/document/d//edit",
            "https://docs.google.com/document/d/bad.id/edit",
        ],
    )
    def test_invalid_google_docs_urls_are_rejected(self, collection, oauth_provider, document_url):
        form = GoogleDocsDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.GOOGLE_DOCS,
                "auto_sync_enabled": False,
                "auth_provider": oauth_provider.id,
                "document_url": document_url,
            },
        )

        assert not form.is_valid()
        assert "document_url" in form.errors

    def test_wrong_source_type_is_rejected(self, collection, oauth_provider):
        form = GoogleDocsDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "auth_provider": oauth_provider.id,
                "document_url": "https://docs.google.com/document/d/doc_123/edit",
            },
        )

        assert not form.is_valid()
        assert "source_type" in form.errors
        assert "config" not in form.cleaned_data

    def test_auth_provider_queryset_is_team_and_type_scoped(self, collection):
        allowed = AuthProviderFactory.create(
            team=collection.team,
            type=AuthProviderType.oauth_authorization_code,
            config={
                "client_id": "client",
                "client_secret": "secret",
                "authorize_url": "https://auth.example.test/authorize",
                "token_url": "https://auth.example.test/token",
                "scope": "documents.readonly",
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )
        wrong_type = AuthProviderFactory.create(team=collection.team, type=AuthProviderType.bearer)
        foreign_team = AuthProviderFactory.create(
            team=TeamFactory.create(),
            type=AuthProviderType.oauth_authorization_code,
            config={
                "client_id": "other",
                "client_secret": "secret",
                "authorize_url": "https://auth.example.test/authorize",
                "token_url": "https://auth.example.test/token",
                "scope": "documents.readonly",
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )

        form = GoogleDocsDocumentSourceForm(collection=collection)
        provider_ids = set(form.fields["auth_provider"].queryset.values_list("id", flat=True))

        assert provider_ids == {allowed.id}
        assert wrong_type.id not in provider_ids
        assert foreign_team.id not in provider_ids
        assert "documents.readonly" in form.fields["auth_provider"].help_text

    @pytest.mark.parametrize(
        ("provider_type", "same_team"),
        [
            (AuthProviderType.bearer, True),
            (AuthProviderType.oauth_authorization_code, False),
        ],
    )
    def test_foreign_or_wrong_type_provider_cannot_be_submitted(self, collection, provider_type, same_team):
        provider = AuthProviderFactory.create(
            team=collection.team if same_team else TeamFactory.create(),
            type=provider_type,
            config=(
                {"token": "bearer-token"}
                if provider_type == AuthProviderType.bearer
                else {
                    "client_id": "other",
                    "client_secret": "secret",
                    "authorize_url": "https://auth.example.test/authorize",
                    "token_url": "https://auth.example.test/token",
                    "scope": "documents.readonly",
                    "token_endpoint_auth_method": "client_secret_basic",
                }
            ),
        )
        form = GoogleDocsDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.GOOGLE_DOCS,
                "auto_sync_enabled": False,
                "auth_provider": provider.id,
                "document_url": "https://docs.google.com/document/d/doc_123/edit",
            },
        )

        assert not form.is_valid()
        assert "auth_provider" in form.errors

    def test_existing_google_docs_config_populates_edit_form(self, collection, oauth_provider):
        url = "https://docs.google.com/document/d/doc_123/edit"
        instance = DocumentSourceFactory.create(
            collection=collection,
            source_type=SourceType.GOOGLE_DOCS,
            auth_provider=oauth_provider,
            config=DocumentSourceConfig(google_docs=GoogleDocsSourceConfig(document_url=url)),
        )

        form = GoogleDocsDocumentSourceForm(collection=collection, instance=instance, initial={})

        assert form.initial["document_url"] == url
        assert form._get_config_from_instance(instance) == instance.config.google_docs
