from django.contrib.messages import get_messages
from django_htmx.http import trigger_client_event


class HtmxMessageMiddleware:
    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.htmx:
            trigger_client_event(
                response,
                "djangoMessages",
                {"messages": [{"message": message.message, "tags": message.tags} for message in get_messages(request)]},
            )

        return response
