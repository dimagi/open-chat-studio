import sentry_sdk
import taskbadger


class RequestContextMiddleware:
    """Middleware to set context for Sentry and Taskbadger"""

    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        with taskbadger.current_scope() as tb_scope:
            request.taskbadger_scope = tb_scope
            return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.taskbadger_scope["view_kwargs"] = make_json_safe(view_kwargs)
        if view_kwargs.get("team_slug"):
            with sentry_sdk.configure_scope() as sentry_scope:
                sentry_scope.set_tag("team", view_kwargs["team_slug"])


def make_json_safe(view_kwargs):
    def cast(value):
        if isinstance(value, int | str | bool):
            return value
        return str(value)

    return {k: cast(v) for k, v in view_kwargs.items()}
