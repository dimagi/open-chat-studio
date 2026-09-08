from django.utils.cache import add_never_cache_headers


class SensitiveDataCacheControlMiddleware:
    """Adds a no-store Cache-Control policy to authenticated responses that haven't already set one."""

    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_authenticated = not request.user.is_anonymous
        if is_authenticated and not response.has_header("Cache-Control"):
            add_never_cache_headers(response)
        return response
