class BannerLocationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.view_name_mapping = {
            "pipelines:home": "pipelines",
            "pipelines:new": "pipelines_new",
            "chatbots:chatbots_home": "chatbots_home",
            "chatbots:new": "chatbots_new",
            "assistants:home": "assistants_home",
            "team:manage_team": "team_settings",
        }

    def __call__(self, request):
        request.banner_location = None
        response = self.get_response(request)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(request, "resolver_match") and request.resolver_match:
            view_name = request.resolver_match.view_name
            banner_location = self.view_name_mapping.get(view_name)
            if banner_location:
                request.banner_location = banner_location
        return None
