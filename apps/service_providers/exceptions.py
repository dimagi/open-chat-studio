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
