import httpx
import pydantic

from apps.service_providers.auth_service.schemes import CommCareAuth


class AuthService(pydantic.BaseModel):
    def get_http_client(self):
        return httpx.Client(**self._get_http_client_kwargs())

    def _get_http_client_kwargs(self) -> dict:
        return {}


class CommCareAuthService(AuthService):
    username: str
    api_key: str

    def _get_http_client_kwargs(self) -> dict:
        return {"auth": CommCareAuth(self.username, self.api_key)}
