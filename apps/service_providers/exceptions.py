from collections.abc import Mapping


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


class UnableToLinkFileException(Exception):
    pass


class MessageMediaError(Exception):
    """Raised when fetching, resolving, or interpreting inbound message media fails."""

    pass


class NoTestableModelError(Exception):
    """Raised when a provider has no configured model to test a connection with."""

    pass


class ConnectionTestNotSupportedError(Exception):
    """Raised when a provider type doesn't support the connection test at all (e.g. Voyage
    AI, which is embeddings-only). Deliberately distinct from ServiceProviderConfigError,
    which represents an invalid configuration for a type that does support the test."""

    pass
