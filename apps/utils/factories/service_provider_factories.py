import factory
import factory.django

from apps.service_providers.models import (
    AuthProvider,
    AuthProviderType,
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    LlmProviderTypes,
    MessagingProvider,
    MessagingProviderType,
    TraceProvider,
    TraceProviderType,
    VoiceProvider,
    VoiceProviderType,
)
from apps.utils.factories.team import TeamFactory


class MessagingProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessagingProvider

    name = factory.Sequence(lambda n: f"Test Messaging Provider {n}")
    team = factory.SubFactory(TeamFactory)
    type = MessagingProviderType.twilio
    config = {}


class LlmProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LlmProvider

    team = factory.SubFactory(TeamFactory)
    type = str(LlmProviderTypes.openai)
    name = factory.Sequence(lambda n: f"Test LLM Provider {n}")
    config = factory.Dict({"openai_api_key": factory.Sequence(lambda n: f"test-openai-api-key-{n}")})


class LlmProviderModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LlmProviderModel

    team = factory.SubFactory(TeamFactory)
    type = str(LlmProviderTypes.openai)
    name = factory.Sequence(lambda n: f"test-model-{n}")
    deprecated = False


class EmbeddingProviderModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EmbeddingProviderModel

    team = factory.SubFactory(TeamFactory)
    type = str(LlmProviderTypes.openai)
    name = "text-embedding-3-small"


class VoiceProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VoiceProvider

    team = factory.SubFactory(TeamFactory)
    type = VoiceProviderType.aws
    name = factory.Sequence(lambda n: f"Test Voice Provider {n}")
    config = factory.Dict(
        {
            "aws_access_key_id": factory.Sequence(lambda n: f"test-aws-access-key-id-{n}"),
            "aws_secret_access_key": factory.Sequence(lambda n: f"test-aws-secret-access-key-{n}"),
            "aws_region": "us-east-1",
        }
    )


class AuthProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuthProvider

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Auth Provider {n}")
    type = AuthProviderType.commcare
    config = factory.Dict(
        {
            "username": factory.Sequence(lambda n: f"test-commcare-username-{n}"),
            "api_key": factory.Sequence(lambda n: f"test-commcare-api-key-{n}"),
        }
    )


class TraceProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TraceProvider

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Trace Provider {n}")
    type = TraceProviderType.langfuse
    config = factory.Dict(
        {
            "public_key": factory.Sequence(lambda n: f"test-langfuse-public-key-{n}"),
            "secret_key": factory.Sequence(lambda n: f"test-langfuse-secret-key-{n}"),
            "host": "https://example.com",
        }
    )
