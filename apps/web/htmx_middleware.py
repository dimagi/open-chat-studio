import json

from django.contrib.messages import get_messages


class HtmxMessageMiddleware:
    def __init__(self, get_response):
        if get_response is None:
            raise ValueError("get_response must be provided.")
        self.get_response = get_response

    def __call__(self, request):
        request.is_htmx = request.headers.get("HX-Request") == "true"
        response = self.get_response(request)
        if request.is_htmx:
            response.headers["HX-Trigger"] = json.dumps(
                {"messages": [{"message": message.message, "tags": message.tags} for message in get_messages(request)]}
            )

        return response
