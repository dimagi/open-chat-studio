from collections.abc import Mapping
from contextlib import contextmanager


def provider_error_message(exc: Exception) -> str:
    """Return the provider's own explanation of a failure, falling back to the exception text.

    Provider SDKs put the parsed error object on `body`, so the useful sentence is there
    rather than in `str(exc)`, which wraps it in the status code and the whole payload.
    Read duck-typed so callers stay free of provider SDK imports.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        message = body.get("message")
        if isinstance(message, str) and message:
            return message
    return str(exc)


class ServiceProviderConfigError(Exception):
    def __init__(self, provider_type: str, message: str):
        self.provider_type = provider_type
        super().__init__(f"[{provider_type}] provider config error: {message}")


class VoiceSyncError(Exception):
    """Raised when a voice sync fails for a reason the user can fix (e.g. API key lacks permissions).

    The message is safe to show to the user.
    """

    DEFAULT_MESSAGE = (
        "The ElevenLabs API key was rejected. Check that it is valid and has the voices_read permission."
    )

    @classmethod
    def from_elevenlabs_auth_error(cls, exc: Exception) -> "VoiceSyncError":
        """Build from an ElevenLabs auth error, whose explanation is nested under body['detail']['message']."""
        body = getattr(exc, "body", None)
        detail = body.get("detail") if isinstance(body, Mapping) else None
        if isinstance(detail, Mapping):
            detail = detail.get("message")
        return cls(detail if isinstance(detail, str) and detail else cls.DEFAULT_MESSAGE)


@contextmanager
def elevenlabs_auth_errors_as_voice_sync_error():
    """Re-raise ElevenLabs 401/403 errors (e.g. key missing voices_read) as a user-facing VoiceSyncError."""
    try:
        yield
    except Exception as e:
        if getattr(e, "status_code", None) in (401, 403):
            raise VoiceSyncError.from_elevenlabs_auth_error(e) from e
        raise


class UnableToLinkFileException(Exception):
    pass


class MessageMediaError(Exception):
    """Raised when fetching, resolving, or interpreting inbound message media fails."""


class NoTestableModelError(Exception):
    """Raised when a provider has no configured model to test a connection with."""


class ConnectionTestNotSupportedError(Exception):
    """Raised when a provider type doesn't support the connection test at all (e.g. Voyage
    AI, which is embeddings-only). Deliberately distinct from ServiceProviderConfigError,
    which represents an invalid configuration for a type that does support the test."""
