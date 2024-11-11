import httpx


class HeaderAuth(httpx.Auth):
    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value

    def auth_flow(self, request):
        request.headers[self.key] = self.value
        yield request


class CommCareAuth(httpx.Auth):
    def __init__(self, username: str, api_key: str):
        self.username = username
        self.api_key = api_key

    def auth_flow(self, request):
        request.headers["Authorization"] = f"ApiKey {self.username}:{self.api_key}"
        yield request
