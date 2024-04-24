class ServiceProviderConfigError(Exception):
    def __init__(self, provider_type: str, message: str):
        self.provider_type = provider_type
        super().__init__(f"[{provider_type}] provider config error: {message}")


class UserServiceProviderConfigError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
