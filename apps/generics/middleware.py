from asgiref.sync import iscoroutinefunction, markcoroutinefunction


class OriginDetectionMiddleware:
    """This is a temporary middleware to aid in the migration from 'experiments' to 'chatbots'"""

    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        self._set_origin(request)
        response = self.get_response(request)
        return response

    async def __acall__(self, request):
        self._set_origin(request)
        response = await self.get_response(request)
        return response

    def _set_origin(self, request):
        if "chatbots" in request.path:
            request.origin = "chatbots"
        elif "experiments" in request.path:
            request.origin = "experiments"
        else:
            request.origin = None
