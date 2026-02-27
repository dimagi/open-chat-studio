from typing import Any

import httpx
import pydantic
import tenacity

from apps.service_providers.auth_service.schemes import CommCareAuth, HeaderAuth


class AuthService(pydantic.BaseModel):
    def get_http_client(self) -> httpx.Client:
        kwargs = {
            **self._get_http_client_kwargs(),
            "timeout": 10,
            "limits": httpx.Limits(max_keepalive_connections=5, max_connections=10),
        }
        return httpx.Client(**kwargs)  # ty: ignore[invalid-argument-type]

    def _get_http_client_kwargs(self) -> dict:
        return {}

    def call_with_retries(self, func, *args, **kwargs) -> Any:
        controller = self.get_retry_controller()
        if controller:
            func = controller.wraps(func)
        return func(*args, **kwargs)

    def get_retry_controller(self):
        return tenacity.Retrying(
            reraise=True,
            stop=tenacity.stop_after_attempt(3),
            wait=wait_or(wait_from_header("Retry-After"), self._default_retry_wait()),
            retry=tenacity.retry_if_exception(self._is_http_429),
        )

    @staticmethod
    def _is_http_429(exc: BaseException) -> bool:
        return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429

    def _default_retry_wait(self):
        return tenacity.wait.wait_exponential_jitter()

    def get_auth_headers(self) -> dict:
        """Extract auth headers from the auth object by constructing a dummy request and auth flow."""
        kwargs = self._get_http_client_kwargs()
        if auth := kwargs.get("auth"):
            request = httpx.Request("GET", "")
            auth_flow = auth.auth_flow(request)
            auth_request = next(auth_flow)
            return dict(auth_request.headers)
        return {}


class BasicAuthService(AuthService):
    username: str
    password: pydantic.SecretStr

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": httpx.BasicAuth(self.username, self.password.get_secret_value())}


class ApiKeyAuthService(AuthService):
    key: str
    value: pydantic.SecretStr

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": HeaderAuth(self.key, self.value.get_secret_value())}


class BearerTokenAuthService(AuthService):
    token: pydantic.SecretStr

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": HeaderAuth("Authorization", f"Bearer {self.token.get_secret_value()}")}


class CommCareAuthService(AuthService):
    username: str
    api_key: pydantic.SecretStr

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": CommCareAuth(self.username, self.api_key.get_secret_value())}


class wait_or(tenacity.wait.wait_base):
    """Return's the first non-zero wait time from the given strategies."""

    def __init__(self, *strategies: tenacity.wait.wait_base):
        self.strategies = strategies

    def __call__(self, retry_state: tenacity.RetryCallState) -> float:
        for strategy in self.strategies:
            wait_time = strategy(retry_state)
            if wait_time:
                return wait_time
        return 0


class wait_from_header(tenacity.wait.wait_base):
    def __init__(self, header: str):
        self.header = header

    def __call__(self, retry_state: tenacity.RetryCallState) -> float:
        if retry_state.outcome is None:
            raise RuntimeError("called before outcome was set")

        if retry_state.outcome.failed:
            exception = retry_state.outcome.exception()
            if exception is None:
                raise RuntimeError("outcome failed but the exception is None")
            retry_after = exception.response.headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
        return 0
