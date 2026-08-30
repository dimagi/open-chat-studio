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
