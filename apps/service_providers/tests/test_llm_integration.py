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
from apps.service_providers.llm_service.default_models import get_default_model
from apps.service_providers.models import LlmProvider, LlmProviderModel, LlmProviderTypes
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory

pytestmark = pytest.mark.integration

# Load environment variables using django-environ
env = environ.Env()

# Try to load .env.integration if it exists, otherwise use regular .env
integration_env = os.path.join(settings.BASE_DIR, ".env.integration")
if os.path.exists(integration_env):
    env.read_env(integration_env)
else:
    env.read_env(os.path.join(settings.BASE_DIR, ".env"))


@pytest.fixture()
def openai_credentials():
    """Get real OpenAI credentials from environment"""
    api_key = env.str("OPENAI_API_KEY", default=None)
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {
        "openai_api_key": api_key,
        "openai_api_base": env.str("OPENAI_API_BASE", default=None),
        "openai_organization": env.str("OPENAI_ORGANIZATION", default=None),
    }


@pytest.fixture()
def anthropic_credentials():
    """Get real Anthropic credentials from environment"""
    api_key = env.str("ANTHROPIC_API_KEY", default=None)
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {"anthropic_api_key": api_key, "anthropic_api_base": "https://api.anthropic.com"}


@pytest.fixture()
def google_credentials():
    """Get real Google Gemini credentials from environment"""
    api_key = env.str("GOOGLE_API_KEY", default=None)
    if not api_key:
        pytest.skip("GOOGLE_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {
        "google_api_key": api_key,
    }


@pytest.fixture()
def google_vertex_ai_credentials():
    """Get real Google Vertex AI credentials from environment"""
    import json

    credentials_json_str = env.str("GOOGLE_VERTEX_AI_CREDENTIALS_JSON", default=None)
    if not credentials_json_str:
        pytest.skip("GOOGLE_VERTEX_AI_CREDENTIALS_JSON not set")  # ty: ignore[invalid-argument-type]

    try:
        credentials_json = json.loads(credentials_json_str)
    except json.JSONDecodeError:
        pytest.skip("GOOGLE_VERTEX_AI_CREDENTIALS_JSON is not valid JSON")  # ty: ignore[invalid-argument-type]

    return {
        "credentials_json": credentials_json,
        "location": env.str("GOOGLE_VERTEX_AI_LOCATION", default="global"),
        "api_transport": env.str("GOOGLE_VERTEX_AI_API_TRANSPORT", default="rest"),
    }


@pytest.fixture()
def deepseek_credentials():
    """Get real DeepSeek credentials from environment"""
    api_key = env.str("DEEPSEEK_API_KEY", default=None)
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {
        "deepseek_api_key": api_key,
    }


@pytest.fixture()
def azure_credentials():
    """Get real Azure OpenAI credentials from environment"""
    api_key = env.str("AZURE_OPENAI_API_KEY", default=None)
    endpoint = env.str("AZURE_OPENAI_ENDPOINT", default=None)
    if not (api_key and endpoint):
        pytest.skip("AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT not set")  # ty: ignore[invalid-argument-type]
    return {
        "openai_api_key": api_key,
        "openai_api_base": endpoint,
        "openai_api_version": env.str("AZURE_OPENAI_API_VERSION", default="2024-02-15-preview"),
    }


@pytest.fixture()
def groq_credentials():
    """Get real Groq credentials from environment"""
    api_key = env.str("GROQ_API_KEY", default=None)
    if not api_key:
        pytest.skip("GROQ_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {
        "openai_api_key": api_key,
    }


@pytest.fixture()
def perplexity_credentials():
    """Get real Perplexity credentials from environment"""
    api_key = env.str("PERPLEXITY_API_KEY", default=None)
    if not api_key:
        pytest.skip("PERPLEXITY_API_KEY not set")  # ty: ignore[invalid-argument-type]
    return {
        "openai_api_key": api_key,
    }


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
    pipeline = PipelineFactory(team=team_with_users)
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
    pipeline = create_pipeline_model(nodes, pipeline=pipeline)  # ty: ignore[invalid-argument-type]

    # Create experiment and session
    experiment = ExperimentFactory(team=team_with_users, pipeline=pipeline)
    session = ExperimentSessionFactory(experiment=experiment)

    # Run pipeline
    bot = PipelineBot(session=session, experiment=experiment, trace_service=TracingService.empty())  # ty: ignore[invalid-argument-type]
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
