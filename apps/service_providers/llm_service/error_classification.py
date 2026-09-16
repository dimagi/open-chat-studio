"""Separate the LLM provider failures a team must fix from the ones worth retrying.

Providers report an exhausted balance, a revoked key and a withdrawn model through
whatever HTTP status happens to fit: OpenAI bills an empty balance as a 429 alongside
genuine rate limits, Anthropic as a 400 alongside malformed requests. Status alone
therefore cannot tell "wait and retry" apart from "nothing will work until someone
adds credit", which is why the checks below reach into provider-specific error codes
and, where Anthropic offers nothing else, the message text. See ADR-0067.
"""

import contextlib
from collections.abc import Iterator

import anthropic
import openai
from google.api_core import exceptions as google_exceptions
from langchain_core.exceptions import ContextOverflowError

from apps.chat.exceptions import ProviderConfigurationError

# OpenAI reports an exhausted balance as a 429, the same status as a genuine rate limit,
# distinguished only by these codes.
OPENAI_QUOTA_CODES = frozenset({"insufficient_quota", "credit_balance_exhausted"})

# Anthropic reports billing and usage caps as a generic 400 invalid_request_error, so the
# message is the only discriminator it offers.
ANTHROPIC_QUOTA_PHRASES = (
    "credit balance is too low",
    "reached your specified api usage limits",
)

# Google reports a bad key as INVALID_ARGUMENT, the same status it uses for a malformed
# request, so only the wording separates them.
GOOGLE_BAD_KEY_PHRASES = (
    "api key not valid",
    "api key expired",
    "invalid api key",
)

AUTHENTICATION_ERRORS: tuple[type[Exception], ...] = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    google_exceptions.Unauthenticated,
    google_exceptions.PermissionDenied,
)

NOT_FOUND_ERRORS: tuple[type[Exception], ...] = (
    openai.NotFoundError,
    anthropic.NotFoundError,
    google_exceptions.NotFound,
)

# LangChain's provider-agnostic base, raised by langchain-openai for both its variants.
CONTEXT_OVERFLOW_ERRORS: tuple[type[Exception], ...] = (ContextOverflowError,)

BILLING_MESSAGE = "The LLM provider account has no credit or quota remaining:"
AUTHENTICATION_MESSAGE = "The LLM provider rejected the credentials configured for this chatbot:"
NOT_FOUND_MESSAGE = "The LLM provider could not find the model this chatbot is configured to use:"
CONTEXT_OVERFLOW_MESSAGE = "The conversation exceeds the model's context window:"


def translate_provider_error(error: BaseException) -> ProviderConfigurationError | None:
    """Return the team-actionable error this provider exception represents, or None.

    None covers both "transient, so leave the native type alone for the retry policy"
    and "not a provider error at all".

    The whole ``__cause__`` chain is examined because LangChain's provider adapters
    re-raise the SDK exception wrapped in one of their own -- langchain-google-genai
    turns every ``InvalidArgument``, a bad API key among them, into a
    ``ChatGoogleGenerativeAIError`` -- and the outer type says nothing useful.
    """
    if isinstance(error, ProviderConfigurationError):
        return None
    for cause in _causes(error):
        for classify in (_billing, _authentication, _not_found, _context_overflow):
            if message := classify(cause):
                return ProviderConfigurationError(f"{message} {_detail(cause)}".strip())
    return None


def _causes(error: BaseException, depth: int = 4) -> Iterator[BaseException]:
    """The exception and the ``raise ... from`` chain beneath it, depth-capped."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(seen) < depth and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


@contextlib.contextmanager
def translate_provider_errors():
    """Re-raise team-actionable provider failures as ProviderConfigurationError."""
    try:
        yield
    except Exception as e:
        if translated := translate_provider_error(e):
            raise translated from e
        raise


def _billing(error: BaseException) -> str | None:
    # Google reports an exhausted balance and a per-minute rate limit alike as
    # ResourceExhausted, with nothing but prose to tell them apart, so it stays in
    # RATE_LIMIT_EXCEPTIONS: over-retrying is the cheaper mistake of the two.
    if isinstance(error, openai.RateLimitError) and _openai_codes(error) & OPENAI_QUOTA_CODES:
        return BILLING_MESSAGE
    if isinstance(error, anthropic.BadRequestError) and _mentions(error, ANTHROPIC_QUOTA_PHRASES):
        return BILLING_MESSAGE
    return None


def _authentication(error: BaseException) -> str | None:
    if isinstance(error, AUTHENTICATION_ERRORS):
        return AUTHENTICATION_MESSAGE
    # Not every InvalidArgument is a bad key; a malformed request is one too, and that is
    # not the team's to fix, so it stays unclassified.
    if isinstance(error, google_exceptions.InvalidArgument) and _mentions(error, GOOGLE_BAD_KEY_PHRASES):
        return AUTHENTICATION_MESSAGE
    return None


def _not_found(error: BaseException) -> str | None:
    if isinstance(error, NOT_FOUND_ERRORS):
        return NOT_FOUND_MESSAGE
    return None


def _context_overflow(error: BaseException) -> str | None:
    if isinstance(error, CONTEXT_OVERFLOW_ERRORS):
        return CONTEXT_OVERFLOW_MESSAGE
    if isinstance(error, openai.BadRequestError) and "context_length_exceeded" in _openai_codes(error):
        return CONTEXT_OVERFLOW_MESSAGE
    return None


def _openai_codes(error: BaseException) -> set[str]:
    """The body's ``code`` and ``type``, since which one carries the reason varies by endpoint."""
    return {code for code in (getattr(error, "code", None), getattr(error, "type", None)) if code}


def _mentions(error: BaseException, phrases: tuple[str, ...]) -> bool:
    message = str(error).lower()
    return any(phrase in message for phrase in phrases)


def _detail(error: BaseException) -> str:
    return getattr(error, "message", None) or str(error)
