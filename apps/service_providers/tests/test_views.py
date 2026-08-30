from unittest import mock

import pytest
from django.urls import reverse

from apps.service_providers.models import (
    AuthProvider,
    LlmProvider,
    LlmProviderTypes,
    MessagingProvider,
    TraceProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


def factory_for_model(model):
    factory = {
        LlmProvider: LlmProviderFactory,
        VoiceProvider: VoiceProviderFactory,
        MessagingProvider: MessagingProviderFactory,
        AuthProvider: AuthProviderFactory,
        TraceProvider: TraceProviderFactory,
    }.get(model)

    return factory


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_table_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    factory.create_batch(5, team=team_with_users)
    assert provider.model.objects.filter(team=team_with_users).count() == 5

    response = authed_client.get(
        reverse("service_providers:table", kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug})
    )
    assert response.status_code == 200
    assert len(response.context["table"].rows) == 5


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_create_view(provider, team_with_users, authed_client):
    """Test that the create view renders without error."""
    subtype = next(iter(provider.subtype))
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": provider.slug,
                "subtype": str(subtype),
            },
        )
    )
    assert response.status_code == 200


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_update_view(provider, team_with_users, authed_client):
    """Test that the update view renders without error."""
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_delete_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200
    assert provider.model.objects.filter(team=team_with_users).count() == 0


@pytest.mark.django_db()
def test_sync_voices_endpoint(team_with_users, authed_client):
    """POST to sync-voices endpoint should call sync_voices on the provider"""

    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    url = reverse(
        "service_providers:sync_voices",
        kwargs={
            "team_slug": team_with_users.slug,
            "provider_type": "voice",
            "pk": provider.pk,
        },
    )
    with mock.patch.object(VoiceProvider, "sync_voices") as mock_sync:
        response = authed_client.post(url)

    assert response.status_code == 302
    mock_sync.assert_called_once()


@pytest.mark.django_db()
def test_delete_llm_provider_referenced_by_pipeline_nullifies_node_fk(team_with_users, authed_client):
    """Deleting an LLM provider referenced by a pipeline node succeeds (SET_NULL): the node's
    llm_provider FK is nulled, while params (authoritative) is left untouched.

    In practice this only happens for an archived pipeline: the delete guards block removing a
    provider that a live (working) node still references, so the FK is only nulled once the
    pipeline holding the node has been archived and the provider is then deleted.
    """
    provider = LlmProviderFactory(team=team_with_users)
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": provider.id},
        llm_provider=provider,
    )

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert not LlmProvider.objects.filter(pk=provider.pk).exists()
    node.refresh_from_db()
    assert node.llm_provider_id is None
    assert node.params["llm_provider_id"] == provider.id  # params unchanged (authoritative)


@pytest.mark.django_db()
def test_delete_llm_provider_blocked_by_an_evaluator(team_with_users, authed_client):
    """Evaluators reference the provider by FK, so deleting underneath one is blocked.

    Previously they were collected by get_related_objects but silently dropped, leaving the
    evaluator with a nulled FK and nothing to run against — with no warning.
    """
    provider = LlmProviderFactory(team=team_with_users)
    evaluator = EvaluatorFactory.create(team=team_with_users, llm_provider=provider)

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert evaluator.name in response.content.decode()
    assert LlmProvider.objects.filter(pk=provider.pk).exists()
    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id == provider.id


@pytest.mark.django_db()
def test_create_view_shows_empty_state_for_provider_with_no_default_models(team_with_users, authed_client):
    """LiteLLM ships no default models (every backend is install-specific, same as OpenRouter).

    The default-models section must say so rather than rendering nothing, which is
    indistinguishable from a broken page.
    """
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "litellm"},
        )
    )
    assert response.status_code == 200
    assert b"No default models are available for this provider" in response.content


@pytest.mark.django_db()
def test_create_view_still_shows_default_models_for_openai(team_with_users, authed_client):
    """Regression guard: the empty-state message must not appear for a provider that does
    ship default models.

    Creates its own global model row rather than relying on the migration-seeded ones -
    see the "migration-seeded global rows" invariant in AGENTS.md.
    """
    LlmProviderModelFactory(team=None, type=str(LlmProviderTypes.openai), name="test-default-model")
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "openai"},
        )
    )
    assert response.status_code == 200
    assert b"No default models are available for this provider" not in response.content
    assert b"test-default-model" in response.content


@pytest.mark.django_db()
def test_create_view_404_for_filtered_subtype(team_with_users, authed_client, settings):
    """openai_voice_engine is gated by the flag_open_ai_voice_engine flag."""
    settings.SLACK_ENABLED = True  # ensure unrelated filter is off
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": "voice",
                "subtype": VoiceProviderType.openai_voice_engine.value,
            },
        )
    )
    assert response.status_code == 404
