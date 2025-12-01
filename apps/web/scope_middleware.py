import taskbadger
from asgiref.sync import iscoroutinefunction, markcoroutinefunction


class RequestContextMiddleware:
    """Middleware to set context for Sentry and Taskbadger"""

    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        with taskbadger.current_scope() as tb_scope:
            request.taskbadger_scope = tb_scope
            return self.get_response(request)

    async def __acall__(self, request):
        with taskbadger.current_scope() as tb_scope:
            request.taskbadger_scope = tb_scope
            return await self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.taskbadger_scope["view_kwargs"] = make_json_safe(view_kwargs)
        if request.user and not request.user.is_anonymous:
            request.taskbadger_scope["user"] = request.user.username

    async def aprocess_view(self, request, view_func, view_args, view_kwargs):
        request.taskbadger_scope["view_kwargs"] = make_json_safe(view_kwargs)
        if request.user and not request.user.is_anonymous:
            request.taskbadger_scope["user"] = request.user.username


def make_json_safe(view_kwargs):
    def cast(value):
        if isinstance(value, int | str | bool):
            return value
        return str(value)

    return {k: cast(v) for k, v in view_kwargs.items()}
