import taskbadger


class RequestContextMiddleware:
    """Middleware to set context for Taskbadger"""

    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        with taskbadger.current_scope() as tb_scope:
            request.taskbadger_scope = tb_scope
            return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.taskbadger_scope["view_kwargs"] = view_kwargs
