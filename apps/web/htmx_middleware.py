from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.contrib.messages import get_messages
from django_htmx.http import trigger_client_event


class HtmxMessageMiddleware:
    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        response = self.get_response(request)
        return self._process_messages_for_htmx(request, response)

    async def __acall__(self, request):
        response = await self.get_response(request)
        return self._process_messages_for_htmx(request, response)

    def _process_messages_for_htmx(self, request, response):
        messages = get_messages(request)
        if request.htmx and messages:
            trigger_client_event(
                response,
                "djangoMessages",
                {"messages": [{"message": message.message, "tags": message.tags} for message in messages]},
            )

        return response
