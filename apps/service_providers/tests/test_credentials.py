"""Service provider credentials stay out of Sentry, and still reach their clients intact.

``attach_stacktrace=True`` sends stack-frame locals with every event, so a pydantic service model
on the frame is serialized by its repr. The ``EventScrubber`` in ``config/sentry.py`` matches by
exact variable/dict-key name and cannot reach a secret embedded inside that repr string, which is
how an Azure subscription key was published to 24 production events.

Holding credentials as ``pydantic.SecretStr`` closes that, but makes every read site a place where
a missing ``.get_secret_value()`` hands the provider SDK ``SecretStr('**********')`` — which a
mocked SDK accepts happily and only fails in production, as an authentication error. The two
halves below guard each side of that trade.
"""

import contextlib
import typing
from unittest import mock

import pydantic
import pytest

from apps.channels.tasks import validate_twillio_request
from apps.service_providers import messaging_service, speech_service
from apps.service_providers.auth_service.main import AuthService
from apps.service_providers.llm_service import main as llm_service
from apps.service_providers.llm_service.main import LlmService
from apps.service_providers.messaging_service import MessagingService
from apps.service_providers.speech_service import SpeechService

SECRET = "top-secret-credential"
PUBLIC = "public-value"

SERVICE_BASES = (SpeechService, MessagingService, LlmService, AuthService)


def _concrete_services() -> list[type[pydantic.BaseModel]]:
    """Every production service model, base classes included — they can hold fields too."""

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    # Test doubles subclass these bases as well; keep the guard on shipped code only.
    return sorted(
        {
            cls
            for base in SERVICE_BASES
            for cls in (base, *walk(base))
            if cls.__module__.startswith("apps.service_providers.")
        },
        key=lambda cls: cls.__name__,
    )


def _dummy_value(annotation):
    """A stand-in for a required field: the sentinel wherever a credential would live."""
    if annotation is pydantic.SecretStr:
        return SECRET
    if annotation is dict:
        return {"private_key": SECRET}
    if typing.get_origin(annotation) is typing.Literal:
        return typing.get_args(annotation)[0]
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    return PUBLIC


def build(service_cls: type[pydantic.BaseModel]) -> pydantic.BaseModel:
    """A service whose only occurrence of SECRET is its credential."""
    kwargs = {
        name: _dummy_value(field.annotation) for name, field in service_cls.model_fields.items() if field.is_required()
    }
    return service_cls(**kwargs)


# --- the repr must not carry credentials ------------------------------------------------------

# Fields whose name looks like a credential but holds a public identifier: an AWS access key ID is
# the public half of the key pair, and ApiKeyAuthService.key is the header name, not its value.
PUBLIC_IDENTIFIER_FIELDS = {
    ("AWSSpeechService", "aws_access_key_id"),
    ("ApiKeyAuthService", "key"),
}

_SECRET_NAME_PARTS = ("key", "secret", "token", "password", "credential")

SERVICE_PARAMS = [pytest.param(cls, id=cls.__name__) for cls in _concrete_services()]


@pytest.mark.parametrize("service_cls", SERVICE_PARAMS)
def test_credentials_are_absent_from_the_model_repr(service_cls):
    service = build(service_cls)
    assert SECRET not in repr(service)
    assert SECRET not in str(service)


@pytest.mark.parametrize("service_cls", SERVICE_PARAMS)
def test_credential_fields_are_secret_str(service_cls):
    """Catches a new credential field that was never typed as a secret in the first place."""
    for name, field in service_cls.model_fields.items():
        if (service_cls.__name__, name) in PUBLIC_IDENTIFIER_FIELDS:
            continue
        if not any(part in name.lower() for part in _SECRET_NAME_PARTS):
            continue
        assert field.annotation is pydantic.SecretStr or field.repr is False, (
            f"{service_cls.__name__}.{name} holds credential material: type it as pydantic.SecretStr, "
            f"or set repr=False when SecretStr does not fit, so it stays out of Sentry stack-frame locals."
        )


# --- the client must receive the real credential ----------------------------------------------


class _ReachedBoundary(BaseException):
    """Raised in place of the real client call.

    Deliberately a BaseException: several of the call sites under test sit inside ``except
    Exception`` blocks that would otherwise swallow it.
    """


@contextlib.contextmanager
def _boundary(target: str):
    """Patch ``target`` so the first call records its arguments and unwinds."""
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        raise _ReachedBoundary

    with mock.patch(target, side_effect=record):
        yield calls


def _flatten(value):
    if isinstance(value, dict):
        value = value.values()
    if isinstance(value, list | tuple | set | type({}.values())):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


# Shared ways to drive a service as far as its first client call.
synthesize = lambda service: service._synthesize_voice("hello", mock.MagicMock())  # noqa: E731
transcribe = lambda service: service._transcribe_audio(mock.MagicMock())  # noqa: E731
client_property = lambda service: service._client  # noqa: E731
messaging_client = lambda service: service.client  # noqa: E731
chat_model = lambda service: service._chat_model("some-model")  # noqa: E731
local_index = lambda service: service.get_local_index_manager("some-embedding-model")  # noqa: E731
download_media = lambda service: service.download_message_media(  # noqa: E731
    mock.MagicMock(media_url="https://example.com/media")
)


def twilio_signature(service):
    """The one credential read site outside apps/service_providers."""
    channel = mock.MagicMock()
    channel.messaging_provider.get_messaging_service.return_value = service
    return validate_twillio_request(channel, b"body", "request-uri", "signature")


def openai_container_file(service):
    return service.retrieve_generated_files_from_service_provider(
        [
            {
                "type": "non_standard_annotation",
                "value": {
                    "type": "container_file_citation",
                    "file_id": "f-1",
                    "container_id": "c-1",
                    "filename": "out.csv",
                },
            }
        ],
        team_id=1,
    )


AZURE_SPEECH_CONFIG = "azure.cognitiveservices.speech.SpeechConfig"
CHAT_OPENAI = "langchain_openai.chat_models.ChatOpenAI"
AZURE_CHAT_OPENAI = "langchain_openai.chat_models.AzureChatOpenAI"
TWILIO_VALIDATOR = "apps.channels.tasks.RequestValidator"
SPEECH_OPENAI = "apps.service_providers.speech_service.OpenAI"
LLM = "apps.service_providers.llm_service.main"

# (service class, patch target standing in for the provider client, how to reach it)
CREDENTIAL_BOUNDARIES = [
    pytest.param(speech_service.AWSSpeechService, "boto3.Session", synthesize, id="aws-polly"),
    pytest.param(speech_service.AzureSpeechService, AZURE_SPEECH_CONFIG, synthesize, id="azure-synthesize"),
    pytest.param(speech_service.AzureSpeechService, AZURE_SPEECH_CONFIG, transcribe, id="azure-transcribe"),
    pytest.param(speech_service.OpenAISpeechService, SPEECH_OPENAI, client_property, id="openai-speech"),
    pytest.param(speech_service.OpenAIVoiceEngineSpeechService, SPEECH_OPENAI, client_property, id="voice-engine"),
    pytest.param(speech_service.OpenAIVoiceEngineSpeechService, "httpx.post", synthesize, id="voice-engine-synthesis"),
    pytest.param(
        speech_service.ElevenLabsSpeechService,
        "apps.service_providers.speech_service.ElevenLabsClient",
        client_property,
        id="elevenlabs",
    ),
    pytest.param(speech_service.IntronSpeechService, "httpx.Client", synthesize, id="intron"),
    pytest.param(speech_service.MinimaxSpeechService, "httpx.post", synthesize, id="minimax"),
    pytest.param(messaging_service.TwilioService, "twilio.rest.Client", messaging_client, id="twilio-client"),
    pytest.param(messaging_service.TwilioService, "httpx.get", download_media, id="twilio-media"),
    pytest.param(messaging_service.TwilioService, TWILIO_VALIDATOR, twilio_signature, id="twilio-signature"),
    pytest.param(messaging_service.TurnIOService, "turn.TurnClient", messaging_client, id="turnio-client"),
    pytest.param(messaging_service.TurnIOService, "httpx.get", download_media, id="turnio-media"),
    pytest.param(messaging_service.SureAdhereService, "httpx.post", lambda s: s.get_access_token(), id="sureadhere"),
    pytest.param(
        messaging_service.MetaCloudAPIService, "httpx.get", lambda s: s.get_phone_numbers(), id="meta-headers"
    ),
    pytest.param(
        messaging_service.MetaCloudAPIService,
        "httpx.post",
        lambda s: s._upload_media("phone-1", b"data", "audio/ogg"),
        id="meta-media-upload",
    ),
    pytest.param(llm_service.OpenAIGenericService, CHAT_OPENAI, chat_model, id="openai-generic"),
    pytest.param(llm_service.OpenAILlmService, f"{LLM}.OpenAI", lambda s: s.get_raw_client(), id="openai-raw-client"),
    pytest.param(llm_service.OpenAILlmService, f"{LLM}.OpenAILocalIndexManager", local_index, id="openai-index"),
    pytest.param(
        llm_service.OpenAILlmService,
        f"{LLM}.get_openai_container_file_contents",
        openai_container_file,
        id="openai-container-file",
    ),
    pytest.param(llm_service.AzureLlmService, AZURE_CHAT_OPENAI, chat_model, id="azure-llm"),
    pytest.param(llm_service.AnthropicLlmService, "langchain_anthropic.ChatAnthropic", chat_model, id="anthropic"),
    pytest.param(llm_service.DeepSeekLlmService, CHAT_OPENAI, chat_model, id="deepseek"),
    pytest.param(
        llm_service.GoogleLlmService, "langchain_google_genai.ChatGoogleGenerativeAI", chat_model, id="google-llm"
    ),
    pytest.param(llm_service.GoogleLlmService, f"{LLM}.GoogleLocalIndexManager", local_index, id="google-index"),
    pytest.param(llm_service.VoyageAILlmService, f"{LLM}.VoyageAILocalIndexManager", local_index, id="voyage-index"),
]


@pytest.mark.parametrize(("service_cls", "target", "reach_the_client"), CREDENTIAL_BOUNDARIES)
def test_credential_reaches_the_client_unwrapped(service_cls, target, reach_the_client):
    with _boundary(target) as calls, pytest.raises(_ReachedBoundary):
        reach_the_client(build(service_cls))

    delivered = list(_flatten(calls[0]))
    assert not any(isinstance(value, pydantic.SecretStr) for value in delivered), (
        f"{service_cls.__name__} handed {target} a SecretStr: the read site needs .get_secret_value()"
    )
    assert any(isinstance(value, str) and SECRET in value for value in delivered), (
        f"{service_cls.__name__} did not pass its credential to {target} — a masked "
        f"'**********' reaches the provider instead of the real value"
    )
