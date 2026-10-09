import pytest

from apps.service_providers.llm_service.credentials import ProviderCredentials
from apps.service_providers.models import LlmProvider, LlmProviderModel, LlmProviderTypes
from apps.web.management.commands.bootstrap_data import (
    _STUB_LLM_MODEL_NAME,
    _STUB_LLM_PROVIDER_NAME,
    Command,
)


@pytest.fixture()
def command():
    return Command()


@pytest.fixture()
def openai_credentials():
    return ProviderCredentials(LlmProviderTypes.openai, "OpenAI", {"openai_api_key": "sk-real-key"})


@pytest.fixture()
def env_credentials(monkeypatch):
    """Pin what the command sees in the environment, so a developer's own .env can't sway the test."""

    def _set(credentials):
        monkeypatch.setattr(
            "apps.web.management.commands.bootstrap_data.get_provider_credentials_from_env",
            lambda: credentials,
        )

    return _set


@pytest.mark.django_db()
def test_seeds_the_stub_provider_when_no_env_credentials(command, team, env_credentials):
    env_credentials([])

    provider, model = command._seed_llm_providers(team)

    assert provider.name == _STUB_LLM_PROVIDER_NAME
    assert provider.config["openai_api_base"].endswith("/mock-llm/v1")
    assert model.name == _STUB_LLM_MODEL_NAME


@pytest.mark.django_db()
def test_real_credentials_win_over_the_stub_provider(command, team, openai_credentials, env_credentials):
    """A developer who sets OPENAI_API_KEY must get a provider holding that key.

    The stub is seeded first and is also of type "openai", so a lookup by type alone
    finds the stub and silently drops the real key.
    """
    env_credentials([openai_credentials])

    provider, _model = command._seed_llm_providers(team)

    assert provider.name == "OpenAI"
    assert provider.config["openai_api_key"] == "sk-real-key"


@pytest.mark.django_db()
def test_stub_provider_is_still_seeded_alongside_real_credentials(command, team, openai_credentials, env_credentials):
    env_credentials([openai_credentials])

    command._seed_llm_providers(team)

    assert LlmProvider.objects.filter(team=team, name=_STUB_LLM_PROVIDER_NAME).exists()


@pytest.mark.django_db()
def test_an_existing_real_provider_is_reused_rather_than_duplicated(command, team, openai_credentials, env_credentials):
    env_credentials([openai_credentials])
    command._seed_llm_providers(team)

    provider, _model = command._seed_llm_providers(team)

    assert provider.name == "OpenAI"
    assert LlmProvider.objects.filter(team=team, type=str(LlmProviderTypes.openai)).count() == 2


@pytest.mark.django_db()
def test_seeding_twice_creates_one_stub_provider_and_model(command, team, env_credentials):
    env_credentials([])
    command._seed_llm_providers(team)
    command._seed_llm_providers(team)

    assert LlmProvider.objects.filter(team=team, name=_STUB_LLM_PROVIDER_NAME).count() == 1
    assert LlmProviderModel.objects.filter(team=team, name=_STUB_LLM_MODEL_NAME).count() == 1


@pytest.mark.django_db()
def test_reseeding_repoints_an_existing_stub_provider(command, team, env_credentials):
    env_credentials([])
    provider, _model = command._seed_llm_providers(team)
    provider.config = {"openai_api_key": "stub", "openai_api_base": "stale"}
    provider.save(update_fields=["config"])

    reseeded, _model = command._seed_llm_providers(team)

    assert reseeded.config["openai_api_base"].endswith("/mock-llm/v1")


@pytest.mark.django_db()
def test_reseeding_updates_the_stub_model_token_limit(command, team, env_credentials, monkeypatch):
    """The token limit is a value to set, not part of the identity of the stub model."""
    env_credentials([])
    command._seed_llm_providers(team)
    monkeypatch.setattr("apps.web.management.commands.bootstrap_data._STUB_LLM_TOKEN_LIMIT", 64000)

    _provider, model = command._seed_llm_providers(team)

    assert LlmProviderModel.objects.filter(team=team, name=_STUB_LLM_MODEL_NAME).count() == 1
    assert model.max_token_limit == 64000
