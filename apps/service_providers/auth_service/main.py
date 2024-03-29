from typing import Any

import httpx
import pydantic
import tenacity

from apps.service_providers.auth_service.schemes import CommCareAuth


class AuthService(pydantic.BaseModel):
    def get_http_client(self) -> httpx.Client:
        return httpx.Client(**self._get_http_client_kwargs())

    def call_with_retries(self, func, *args, **kwargs) -> Any:
        controller = self.get_retry_controller()
        if controller:
            func = controller.wraps(func)
        return func(*args, **kwargs)

    def get_retry_controller(self) -> tenacity.Retrying | None:
        return None

    def _get_http_client_kwargs(self) -> dict:
        return {}


class CommCareAuthService(AuthService):
    username: str
    api_key: str

    def get_retry_controller(self):
        return tenacity.Retrying(
            reraise=True,
            stop=tenacity.stop_after_attempt(3),
            wait=wait_or(wait_from_header("Retry-After"), self._default_retry_wait()),
            retry=tenacity.retry_if_exception(self._is_http_429),
        )

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": CommCareAuth(self.username, self.api_key)}

    @staticmethod
    def _is_http_429(exc: BaseException) -> bool:
        return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429

    def _default_retry_wait(self):
        return tenacity.wait.wait_exponential_jitter()


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
