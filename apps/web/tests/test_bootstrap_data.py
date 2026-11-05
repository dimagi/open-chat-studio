import pytest

from apps.service_providers import mock_llm
from apps.service_providers.llm_service.credentials import ProviderCredentials
from apps.service_providers.models import LlmProvider, LlmProviderModel, LlmProviderTypes
from apps.web.management.commands.bootstrap_data import _STUB_LLM_PROVIDER_NAME, Command

SERVER_URL = "http://localhost:8000"


@pytest.fixture()
def command():
    return Command()


@pytest.fixture()
def openai_credentials():
    return ProviderCredentials(type=LlmProviderTypes.openai, name="OpenAI", config={"openai_api_key": "sk-real-key"})


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

    provider, model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert provider.name == _STUB_LLM_PROVIDER_NAME
    assert provider.config["openai_api_base"] == "http://localhost:8000/mock-llm/v1"
    assert model.name == mock_llm.MODEL_NAME


@pytest.mark.django_db()
def test_stub_provider_points_at_the_given_server(command, team, env_credentials):
    """Under portless the dev server is not on localhost:8000."""
    env_credentials([])

    provider, _model = command._seed_llm_providers(team=team, base_url="https://ocs.localhost/")

    assert provider.config["openai_api_base"] == "https://ocs.localhost/mock-llm/v1"


@pytest.mark.django_db()
def test_real_credentials_win_over_the_stub_provider(command, team, openai_credentials, env_credentials):
    """A developer who sets OPENAI_API_KEY must get a provider holding that key.

    The stub is seeded first and is also of type "openai", so a lookup by type alone
    finds the stub and silently drops the real key.
    """
    env_credentials([openai_credentials])

    provider, _model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert provider.name == "OpenAI"
    assert provider.config["openai_api_key"] == "sk-real-key"


@pytest.mark.django_db()
def test_stub_provider_is_still_seeded_alongside_real_credentials(command, team, openai_credentials, env_credentials):
    env_credentials([openai_credentials])

    command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert LlmProvider.objects.filter(team=team, name=_STUB_LLM_PROVIDER_NAME).exists()


@pytest.mark.django_db()
def test_an_existing_real_provider_is_reused_rather_than_duplicated(command, team, openai_credentials, env_credentials):
    env_credentials([openai_credentials])
    command._seed_llm_providers(team=team, base_url=SERVER_URL)

    provider, _model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert provider.name == "OpenAI"
    assert LlmProvider.objects.filter(team=team, type=str(LlmProviderTypes.openai)).count() == 2


@pytest.mark.django_db()
def test_seeding_twice_creates_one_stub_provider_and_model(command, team, env_credentials):
    env_credentials([])
    command._seed_llm_providers(team=team, base_url=SERVER_URL)
    command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert LlmProvider.objects.filter(team=team, name=_STUB_LLM_PROVIDER_NAME).count() == 1
    assert LlmProviderModel.objects.filter(team=team, name=mock_llm.MODEL_NAME).count() == 1


@pytest.mark.django_db()
def test_reseeding_repoints_an_existing_stub_provider(command, team, env_credentials):
    env_credentials([])
    provider, _model = command._seed_llm_providers(team=team, base_url=SERVER_URL)
    provider.config = {"openai_api_key": "stub", "openai_api_base": "stale"}
    provider.save(update_fields=["config"])

    reseeded, _model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert reseeded.config["openai_api_base"].endswith("/mock-llm/v1")


@pytest.mark.django_db()
def test_reseeding_updates_the_stub_model_token_limit(command, team, env_credentials, monkeypatch):
    """The token limit is a value to set, not part of the identity of the stub model."""
    env_credentials([])
    command._seed_llm_providers(team=team, base_url=SERVER_URL)
    monkeypatch.setattr("apps.service_providers.mock_llm.MODEL_TOKEN_LIMIT", 64000)

    _provider, model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert LlmProviderModel.objects.filter(team=team, name=mock_llm.MODEL_NAME).count() == 1
    assert model.max_token_limit == 64000


@pytest.mark.django_db()
def test_reseeding_tolerates_another_stub_model_with_a_different_token_limit(command, team, env_credentials):
    env_credentials([])
    _provider, seeded = command._seed_llm_providers(team=team, base_url=SERVER_URL)
    LlmProviderModel.objects.create(
        team=team, name=mock_llm.MODEL_NAME, type=str(LlmProviderTypes.openai), max_token_limit=8192
    )

    _provider, model = command._seed_llm_providers(team=team, base_url=SERVER_URL)

    assert model == seeded
