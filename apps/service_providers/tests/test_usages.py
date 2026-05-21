import pytest
from django.urls import reverse

from apps.service_providers.models import LlmProviderTypes, MessagingProviderType, TraceProviderType, VoiceProviderType
from apps.service_providers.usages import get_provider_usages, search_providers_by_api_key
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


@pytest.fixture()
def anthropic_provider(team_with_users):
    return LlmProviderFactory(
        team=team_with_users,
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "sk-ant-secret-AbCdEf", "anthropic_api_base": "https://api.anthropic.com"},
    )


@pytest.mark.django_db()
def test_get_usages_includes_assistants(anthropic_provider):
    OpenAiAssistantFactory(team=anthropic_provider.team, llm_provider=anthropic_provider)

    usages = get_provider_usages(anthropic_provider)

    category_labels = {c.label for c in usages.categories}
    assert any("Assistant" in label for label in category_labels)
    assert not usages.is_empty()
    assert usages.total >= 1


@pytest.mark.django_db()
def test_get_usages_empty_when_unreferenced(anthropic_provider):
    usages = get_provider_usages(anthropic_provider)
    assert usages.is_empty()
    assert usages.total == 0


@pytest.mark.django_db()
def test_search_exact_match_finds_provider(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_suffix_match(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "AbCdEf", match="suffix")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_contains_match(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "secret", match="contains")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_no_false_positive_on_other_subtype(team_with_users, anthropic_provider):
    LlmProviderFactory(
        team=team_with_users,
        type=str(LlmProviderTypes.openai),
        config={"openai_api_key": "sk-ant-secret-AbCdEf"},
    )
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    # both should match — both contain the value in *their* declared obfuscate fields
    assert len(matches) == 2


@pytest.mark.django_db()
def test_search_ignores_provider_with_different_secret(anthropic_provider):
    LlmProviderFactory(
        team=anthropic_provider.team,
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "other-key"},
    )
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    assert [p.pk for p in matches] == [anthropic_provider.pk]


@pytest.mark.django_db()
def test_search_empty_key_raises():
    with pytest.raises(ValueError, match="non-empty"):
        search_providers_by_api_key(ServiceProvider.llm, "", match="exact")


@pytest.mark.django_db()
def test_search_voice_provider(team_with_users):
    voice = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.aws,
        config={"aws_access_key_id": "AKIA-x", "aws_secret_access_key": "voice-secret"},
    )
    matches = search_providers_by_api_key(ServiceProvider.voice, "voice-secret", match="exact")
    assert voice in matches


@pytest.mark.django_db()
def test_search_messaging_provider(team_with_users):
    msg = MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.twilio,
        config={"auth_token": "twilio-secret", "account_sid": "AC123"},
    )
    matches = search_providers_by_api_key(ServiceProvider.messaging, "twilio-secret", match="exact")
    assert msg in matches


@pytest.mark.django_db()
def test_search_trace_provider(team_with_users):
    trace = TraceProviderFactory(
        team=team_with_users,
        type=TraceProviderType.langfuse,
        config={"public_key": "pk", "secret_key": "trace-secret", "host": "https://example.com"},
    )
    matches = search_providers_by_api_key(ServiceProvider.tracing, "trace-secret", match="exact")
    assert trace in matches


@pytest.mark.django_db()
def test_search_invalid_match_mode_raises(anthropic_provider):
    with pytest.raises(ValueError, match="unknown match mode"):
        search_providers_by_api_key(ServiceProvider.llm, "key", match="fuzzy")  # type: ignore[arg-type]


@pytest.mark.django_db()
def test_usages_view_renders(team_with_users, client, anthropic_provider):
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
    )
    response = client.get(url)
    assert response.status_code == 200
    assert b"Where is" in response.content
