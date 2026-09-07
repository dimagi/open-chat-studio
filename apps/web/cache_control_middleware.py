from django.utils.cache import add_never_cache_headers


class SensitiveDataCacheControlMiddleware:
    """Adds a no-store Cache-Control policy to every authenticated response.

    Applied broadly rather than enumerating specific "sensitive" pages: any authenticated
    response can carry participant or team data, and a per-page allowlist is the kind of
    thing that gets forgotten on new pages. A view that has already set its own
    Cache-Control header (e.g. the experiment trend chart's deliberate browser-side cache)
    is left alone.
    """

    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_authenticated = request.user and not request.user.is_anonymous
        if is_authenticated and not response.has_header("Cache-Control"):
            add_never_cache_headers(response)
        return response
