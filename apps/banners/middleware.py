import re


class BannerLocationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.location_patterns = [
            (r"^/experiments/", "experiments_home"),
            (r"^/pipelines/", "pipelines_home"),
            (r"^/chatbots/", "chatbots_home"),
            (r"^/team/", "team_settings"),
        ]
        self.compiled_patterns = [(re.compile(pattern), location) for pattern, location in self.location_patterns]

    def __call__(self, request):
        request.banner_location = None
        path = request.path
        for pattern, location in self.compiled_patterns:
            if pattern.match(path):
                request.banner_location = location
                break
        response = self.get_response(request)
        return response
