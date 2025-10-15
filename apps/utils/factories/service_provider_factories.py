import factory

from apps.service_providers.models import (
    AuthProvider,
    AuthProviderType,
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    LlmProviderTypes,
    MessagingProvider,
    TraceProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.utils.factories.team import TeamFactory


class MessagingProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessagingProvider

    name = factory.Sequence(lambda n: f"Test Messaging Provider {n}")
    team = factory.SubFactory(TeamFactory)


class LlmProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LlmProvider

    team = factory.SubFactory(TeamFactory)
    type = str(LlmProviderTypes.openai)
    name = factory.Sequence(lambda n: f"Test LLM Provider {n}")
    config = {"openai_api_key": "123"}


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
    config = {"aws_access_key_id": "123", "aws_secret_access_key": "123", "aws_region": "us-east-1"}


class AuthProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuthProvider

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Auth Provider {n}")
    type = AuthProviderType.commcare
    config = {"username": "user", "api_key": "key"}


class TraceProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TraceProvider

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Trace Provider {n}")
    type = AuthProviderType.commcare
    config = {"public_key": "123", "secret_key": "***", "host": "https://example.com"}
