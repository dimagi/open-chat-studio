from django.contrib.messages import get_messages
from django_htmx.http import trigger_client_event

# The client leaves the page on these, so the next page load renders any messages instead.
CLIENT_NAVIGATION_HEADERS = ("HX-Redirect", "HX-Refresh")


class HtmxMessageMiddleware:
    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if any(header in response.headers for header in CLIENT_NAVIGATION_HEADERS):
            return response
        messages = get_messages(request)
        if request.htmx and messages:
            trigger_client_event(
                response,
                "djangoMessages",
                {"messages": [{"message": message.message, "tags": message.tags} for message in messages]},
            )

        return response
