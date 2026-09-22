import asyncio

import anthropic
import httpx
import openai
import pytest
from google.api_core import exceptions as google_exceptions
from langchain_core.exceptions import ContextOverflowError
from langchain_core.runnables import RunnableLambda

from apps.chat.exceptions import ModelRefusedTurnError, ProviderConfigurationError
from apps.service_providers.llm_service.error_classification import (
    translate_provider_error,
    translate_provider_errors,
)
from apps.service_providers.llm_service.retry import (
    _TranslateProviderErrors,
    should_retry_exception,
    with_llm_retry,
)


def _status_error(cls, status: int, message: str, body: dict | None = None):
    request = httpx.Request("POST", "https://api.example.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=body or {})


def _openai_error(cls, status: int, message: str, code: str | None = None, type_: str | None = None):
    return _status_error(cls, status, message, {"message": message, "code": code, "type": type_})


def _innermost_detail(error: BaseException) -> str:
    while error.__cause__ is not None:
        error = error.__cause__
    # Not every provider exception carries `.message` (LangChain's own ones do not).
    return getattr(error, "message", None) or str(error)


def _wrapped_by_langchain(inner: Exception):
    """langchain-google-genai turns every InvalidArgument into one of its own errors."""
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError  # noqa: PLC0415 - TID253: heavy lib

    try:
        raise inner
    except type(inner) as e:
        try:
            raise ChatGoogleGenerativeAIError(f"Invalid argument provided to Gemini: {e}") from e
        except ChatGoogleGenerativeAIError as wrapper:
            return wrapper


# Each case is an error seen in production, paired with the phrase the team needs to read.
TEAM_ACTIONABLE = [
    pytest.param(
        _openai_error(
            openai.RateLimitError,
            429,
            "You have no credits remaining.",
            code="credit_balance_exhausted",
            type_="insufficient_quota",
        ),
        "no credit or quota remaining",
        id="openai_credits_exhausted",
    ),
    pytest.param(
        _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."),
        "no credit or quota remaining",
        id="anthropic_credit_balance_too_low",
    ),
    pytest.param(
        _status_error(anthropic.BadRequestError, 400, "You have reached your specified API usage limits."),
        "no credit or quota remaining",
        id="anthropic_usage_limit_reached",
    ),
    pytest.param(
        _openai_error(
            openai.AuthenticationError, 401, "Incorrect API key provided: sk-proj-xxx", code="invalid_api_key"
        ),
        "rejected the credentials",
        id="openai_bad_key",
    ),
    pytest.param(
        _status_error(anthropic.AuthenticationError, 401, "API key is invalid."),
        "rejected the credentials",
        id="anthropic_bad_key",
    ),
    pytest.param(
        _status_error(anthropic.NotFoundError, 404, "model: claude-3-5-haiku-latest"),
        "could not find the model",
        id="anthropic_unknown_model",
    ),
    pytest.param(
        google_exceptions.NotFound("This model models/gemini-2.5-flash is no longer available to new users."),
        "could not find the model",
        id="google_withdrawn_model",
    ),
    pytest.param(
        google_exceptions.Unauthenticated("API keys are not supported by this API."),
        "rejected the credentials",
        id="google_unauthenticated",
    ),
    pytest.param(
        google_exceptions.PermissionDenied("Generative Language API has not been used in project 1234."),
        "rejected the credentials",
        id="google_permission_denied",
    ),
    pytest.param(
        _wrapped_by_langchain(google_exceptions.InvalidArgument("API key not valid. Please pass a valid API key.")),
        "rejected the credentials",
        id="google_bad_key_wrapped_by_langchain",
    ),
    pytest.param(
        _openai_error(
            openai.BadRequestError,
            400,
            "Your input exceeds the context window of this model.",
            code="context_length_exceeded",
        ),
        "context window",
        id="openai_context_overflow",
    ),
    pytest.param(
        ContextOverflowError("Your input exceeds the context window of this model."),
        "context window",
        id="langchain_context_overflow",
    ),
]

# Transient or unrelated: these must keep their native type so the retry policy still sees them.
LEFT_ALONE = [
    pytest.param(
        _openai_error(openai.RateLimitError, 429, "Rate limit reached", code="rate_limit_exceeded"),
        True,
        id="openai_genuine_rate_limit",
    ),
    pytest.param(_status_error(anthropic.OverloadedError, 529, "Overloaded"), True, id="anthropic_overloaded"),
    pytest.param(ValueError("boom"), False, id="unrelated_exception"),
    # A malformed request is wrapped the same way a bad key is, but nobody on the team can fix it.
    pytest.param(
        _wrapped_by_langchain(google_exceptions.InvalidArgument("Unable to submit request: empty text parameter.")),
        False,
        id="google_malformed_request_wrapped",
    ),
]


@pytest.mark.parametrize(("error", "expected_phrase"), TEAM_ACTIONABLE)
def test_team_actionable_errors_are_translated(error, expected_phrase):
    translated = translate_provider_error(error)

    assert isinstance(translated, ProviderConfigurationError)
    assert expected_phrase in str(translated)
    # The provider's own wording is preserved so the team can act without opening Sentry.
    # It comes from whichever exception in the chain was classified, which for an error a
    # LangChain adapter has wrapped is the cause rather than the wrapper.
    assert _innermost_detail(error) in str(translated)


@pytest.mark.parametrize(("error", "_expected_phrase"), TEAM_ACTIONABLE)
def test_team_actionable_errors_are_not_retried(error, _expected_phrase):
    assert should_retry_exception(error) is False


@pytest.mark.parametrize(("error", "retryable"), LEFT_ALONE)
def test_other_errors_keep_their_native_type(error, retryable):
    assert translate_provider_error(error) is None
    assert should_retry_exception(error) is retryable


def test_an_already_translated_error_is_left_alone():
    """Otherwise a second boundary would rebuild it from its own cause."""
    original = _status_error(anthropic.AuthenticationError, 401, "API key is invalid.")
    translated = translate_provider_error(original)

    assert translate_provider_error(translated) is None


def test_context_manager_translates_and_chains():
    error = _status_error(anthropic.AuthenticationError, 401, "API key is invalid.")

    with pytest.raises(ProviderConfigurationError) as exc_info:  # noqa: SIM117
        with translate_provider_errors():
            raise error

    assert exc_info.value.__cause__ is error


def test_context_manager_reraises_unclassified_errors():
    with pytest.raises(ValueError, match="boom"):  # noqa: SIM117
        with translate_provider_errors():
            raise ValueError("boom")


@pytest.fixture()
def no_retry_backoff(monkeypatch):
    """tenacity really sleeps between attempts; the tests below only count them."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def _always_raises(error):
    attempts = []

    def raise_it(_input):
        attempts.append(1)
        raise error

    return RunnableLambda(raise_it), attempts


# RunnableSequence.stream drives its steps through transform and RunnableRetry batches with
# return_exceptions=True, so which entry point runs is not the caller's choice.
ENTRY_POINTS = [
    pytest.param(lambda r: r.invoke("hi"), id="invoke"),
    pytest.param(lambda r: list(r.stream("hi")), id="stream"),
    pytest.param(lambda r: list(r.transform(iter(["hi"]))), id="transform"),
    pytest.param(lambda r: r.batch(["hi"]), id="batch"),
    pytest.param(lambda r: asyncio.run(r.ainvoke("hi")), id="ainvoke"),
    pytest.param(lambda r: asyncio.run(_collect(r.astream("hi"))), id="astream"),
    pytest.param(lambda r: asyncio.run(_collect(r.atransform(_aiter(["hi"])))), id="atransform"),
    pytest.param(lambda r: asyncio.run(r.abatch(["hi"])), id="abatch"),
]


async def _collect(async_iterator):
    return [chunk async for chunk in async_iterator]


async def _aiter(items):
    for item in items:
        yield item


@pytest.mark.parametrize("call", ENTRY_POINTS)
def test_with_llm_retry_translates_and_does_not_retry_on_every_entry_point(call, no_retry_backoff):
    error = _openai_error(openai.RateLimitError, 429, "You have no credits remaining.", code="credit_balance_exhausted")
    runnable, attempts = _always_raises(error)

    with pytest.raises(ProviderConfigurationError):
        call(with_llm_retry(runnable, max_attempts=3))

    assert len(attempts) == 1


def test_with_llm_retry_still_retries_a_genuine_rate_limit(no_retry_backoff):
    error = _openai_error(openai.RateLimitError, 429, "Rate limit reached", code="rate_limit_exceeded")
    runnable, attempts = _always_raises(error)

    with pytest.raises(openai.RateLimitError):
        with_llm_retry(runnable, max_attempts=3).invoke("hi")

    assert len(attempts) == 3


def test_translation_wrapper_preserves_bound_kwargs():
    """The overrides delegate through super(), so RunnableBinding still merges what was bound.

    Going straight to self.bound would drop these silently, with nothing to signal it.
    """
    seen = {}

    def capture(_input, **kwargs):
        seen.update(kwargs)
        return "ok"

    wrapped = _TranslateProviderErrors(bound=RunnableLambda(capture), kwargs={"stop": ["x"]})

    assert wrapped.invoke("hi") == "ok"
    assert seen == {"stop": ["x"]}


AZURE_FILTER_BODY = {
    "code": "content_filter",
    "message": "The response was filtered",
    "param": "prompt",
    "type": None,
    "status": 400,
    "innererror": {
        "code": "ResponsibleAIPolicyViolation",
        "content_filter_result": {"hate": {"filtered": True, "severity": "high"}},
    },
}


def _content_filter_error(body):
    request = httpx.Request("POST", "https://example.openai.azure.com/openai/deployments/x/chat/completions")
    return openai.BadRequestError(
        "Error code: 400 - content filter", response=httpx.Response(400, request=request), body=body
    )


class TestContentFilter400:
    def test_is_translated_to_a_participant_actionable_error(self):
        translated = translate_provider_error(_content_filter_error(AZURE_FILTER_BODY))

        assert isinstance(translated, ModelRefusedTurnError)
        assert translated.kind == "content_filter"
        assert translated.provider_reason == "content_filter"
        assert translated.detail == {"hate": {"filtered": True, "severity": "high"}}

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"code": "content_filter"}, id="no_innererror"),
            pytest.param({"code": "content_filter", "innererror": None}, id="innererror_none"),
            pytest.param("The response was filtered", id="body_is_text"),
            pytest.param(None, id="body_is_none"),
        ],
    )
    def test_detail_tolerates_missing_or_non_dict_bodies(self, body):
        error = _content_filter_error(body)
        if not isinstance(body, dict):
            # The SDK only reads ``code`` from a dict body; set it the way a real error carries it.
            error.code = "content_filter"

        translated = translate_provider_error(error)

        assert isinstance(translated, ModelRefusedTurnError)
        assert translated.detail == {}

    def test_is_not_retried(self):
        assert should_retry_exception(_content_filter_error(AZURE_FILTER_BODY)) is False

    def test_an_already_translated_refusal_is_left_alone(self):
        translated = translate_provider_error(_content_filter_error(AZURE_FILTER_BODY))

        assert translate_provider_error(translated) is None

    def test_context_manager_translates_and_chains(self):
        error = _content_filter_error(AZURE_FILTER_BODY)

        with pytest.raises(ModelRefusedTurnError) as exc_info:  # noqa: SIM117
            with translate_provider_errors():
                raise error

        assert exc_info.value.__cause__ is error

    def test_with_llm_retry_translates_without_retrying(self, no_retry_backoff):
        error = _content_filter_error(AZURE_FILTER_BODY)
        calls = []

        def raising(_input):
            calls.append(1)
            raise error

        with pytest.raises(ModelRefusedTurnError):
            with_llm_retry(RunnableLambda(raising)).invoke("x")

        assert len(calls) == 1
