import httpx


class CommCareAuth(httpx.Auth):
    def __init__(self, username: str, api_key: str):
        self.username = username
        self.api_key = api_key

    def auth_flow(self, request):
        request.headers["Authorization"] = f"ApiKey {self.username}:{self.api_key}"
        yield request
