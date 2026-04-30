import os

import environ
import pytest
from django.conf import settings

from apps.chat.bots import PipelineBot
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.tests.utils import (
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.service_providers.llm_service.credentials import get_provider_credentials_for_type
from apps.service_providers.llm_service.default_models import get_default_model
from apps.service_providers.models import LlmProvider, LlmProviderModel, LlmProviderTypes
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory

pytestmark = pytest.mark.integration

# Load env file (.env.integration if present, else .env) so the credentials helper can read os.environ.
_env_file = os.path.join(settings.BASE_DIR, ".env.integration")
if not os.path.exists(_env_file):
    _env_file = os.path.join(settings.BASE_DIR, ".env")
environ.Env().read_env(_env_file)


def _credentials_or_skip(provider_type: LlmProviderTypes, missing_env: str) -> dict:
    """Return the provider config from env, or skip the test if it isn't configured."""
    creds = get_provider_credentials_for_type(provider_type)
    if not creds:
        pytest.skip(f"{missing_env} not set")
    return creds.config


@pytest.fixture()
def openai_credentials():
    return _credentials_or_skip(LlmProviderTypes.openai, "OPENAI_API_KEY")


@pytest.fixture()
def anthropic_credentials():
    return _credentials_or_skip(LlmProviderTypes.anthropic, "ANTHROPIC_API_KEY")


@pytest.fixture()
def google_credentials():
    return _credentials_or_skip(LlmProviderTypes.google, "GOOGLE_API_KEY")


@pytest.fixture()
def google_vertex_ai_credentials():
    return _credentials_or_skip(LlmProviderTypes.google_vertex_ai, "GOOGLE_VERTEX_AI_CREDENTIALS_JSON")


@pytest.fixture()
def deepseek_credentials():
    return _credentials_or_skip(LlmProviderTypes.deepseek, "DEEPSEEK_API_KEY")


@pytest.fixture()
def azure_credentials():
    return _credentials_or_skip(LlmProviderTypes.azure, "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT")


@pytest.fixture()
def groq_credentials():
    return _credentials_or_skip(LlmProviderTypes.groq, "GROQ_API_KEY")


@pytest.fixture()
def perplexity_credentials():
    return _credentials_or_skip(LlmProviderTypes.perplexity, "PERPLEXITY_API_KEY")


def _run_llm_pipeline_test(
    team_with_users,
    provider_type: LlmProviderTypes,
    provider_config: dict,
    test_prompt: str = "Say 'test successful' and nothing else.",
    model_name: str | None = None,
):
    """Helper function to run a basic LLM pipeline test"""
    # Get default model for provider if not specified
    if model_name is None:
        default_model = get_default_model(str(provider_type))
        model_name = default_model.name

    # Create LLM provider
    provider = LlmProvider.objects.create(
        team=team_with_users,
        type=str(provider_type),
        name=f"Test {provider_type.label}",
        config=provider_config,
    )

    # Create LLM provider model
    provider_model = LlmProviderModel.objects.create(
        team=team_with_users,
        type=str(provider_type),
        name=model_name,
    )

    # Create pipeline with single LLM node
    pipeline = PipelineFactory.create(team=team_with_users)
    nodes = [
        start_node(),
        llm_response_with_prompt_node(
            provider_id=provider.id,
            provider_model_id=provider_model.id,
            prompt=test_prompt,
            name="llm",
        ),
        end_node(),
    ]
    pipeline = create_pipeline_model(nodes, pipeline=pipeline)

    # Create experiment and session
    experiment = ExperimentFactory.create(team=team_with_users, pipeline=pipeline)
    session = ExperimentSessionFactory.create(experiment=experiment)

    # Run pipeline
    bot = PipelineBot(session=session, experiment=experiment, trace_service=TracingService.empty())
    input_state = PipelineState(messages=["Hello"], experiment_session=session)
    ai_message = bot.invoke_pipeline(input_state=input_state, pipeline=pipeline)

    # Verify output
    assert ai_message is not None
    assert ai_message.content
    assert len(ai_message.content) > 0

    print(f"{provider_type} response from model {model_name}: {ai_message.content}")


@pytest.mark.django_db()
class TestOpenAIIntegration:
    """Integration tests for OpenAI LLM provider"""

    def test_openai_chat_completion(self, team_with_users, openai_credentials):
        """Test OpenAI chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.openai,
            provider_config=openai_credentials,
        )


@pytest.mark.django_db()
class TestAnthropicIntegration:
    """Integration tests for Anthropic LLM provider"""

    def test_anthropic_chat_completion(self, team_with_users, anthropic_credentials):
        """Test Anthropic chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.anthropic,
            provider_config=anthropic_credentials,
        )


@pytest.mark.django_db()
class TestGoogleGeminiIntegration:
    """Integration tests for Google Gemini LLM provider"""

    def test_google_gemini_chat_completion(self, team_with_users, google_credentials):
        """Test Google Gemini chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.google,
            provider_config=google_credentials,
        )


@pytest.mark.django_db()
class TestGoogleVertexAIIntegration:
    """Integration tests for Google Vertex AI LLM provider"""

    def test_google_vertex_ai_chat_completion(self, team_with_users, google_vertex_ai_credentials):
        """Test Google Vertex AI chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.google_vertex_ai,
            provider_config=google_vertex_ai_credentials,
        )


@pytest.mark.django_db()
class TestDeepSeekIntegration:
    """Integration tests for DeepSeek LLM provider"""

    def test_deepseek_chat_completion(self, team_with_users, deepseek_credentials):
        """Test DeepSeek chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.deepseek,
            provider_config=deepseek_credentials,
        )


@pytest.mark.django_db()
class TestAzureOpenAIIntegration:
    """Integration tests for Azure OpenAI LLM provider"""

    def test_azure_openai_chat_completion(self, team_with_users, azure_credentials):
        """Test Azure OpenAI chat completion through pipeline"""
        # Note: Azure uses deployment names which may vary by setup
        # Allow override via env var, otherwise use default model
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.azure,
            provider_config=azure_credentials,
        )


@pytest.mark.django_db()
class TestGroqIntegration:
    """Integration tests for Groq LLM provider"""

    def test_groq_chat_completion(self, team_with_users, groq_credentials):
        """Test Groq chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.groq,
            provider_config=groq_credentials,
            model_name="llama-3.1-8b-instant",  # The current default model has been removed
        )


@pytest.mark.django_db()
class TestPerplexityIntegration:
    """Integration tests for Perplexity LLM provider"""

    def test_perplexity_chat_completion(self, team_with_users, perplexity_credentials):
        """Test Perplexity chat completion through pipeline"""
        _run_llm_pipeline_test(
            team_with_users=team_with_users,
            provider_type=LlmProviderTypes.perplexity,
            provider_config=perplexity_credentials,
        )
