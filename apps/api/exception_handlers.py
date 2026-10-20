"""DRF exception handlers for the API (#2140 / #2349).

Kept out of apps.api.throttling: importing rest_framework.views evaluates the
APIView class body, which resolves DEFAULT_THROTTLE_CLASSES and so imports the
throttling module. EXCEPTION_HANDLER is resolved per request instead, so this
module can import rest_framework.views at module level without a cycle.
"""

import re

from django.apps import apps
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

# Django's `get_object_or_404` names the model's class; v2 names resources as its endpoints do.
_DJANGO_NOT_FOUND = re.compile(r"^No (?P<model>\w+) matches the given query\.$")
_V2_RESOURCE_NAMES = {"Experiment": "chatbot", "Node": "pipeline node"}


def api_exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        return Response(
            {"detail": f"Request body too large. The maximum is {settings.DATA_UPLOAD_MAX_MEMORY_SIZE} bytes."},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    # A bare Http404 is left alone so DRF answers its default "Not found."
    if isinstance(exc, Http404) and str(exc) and getattr(context.get("request"), "version", None) == "v2":
        exc = Http404(_v2_not_found_message(str(exc)))

    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        response.data = {"detail": "Rate limit exceeded.", "available_in": int(exc.wait or 0)}
    return response


def _v2_not_found_message(message: str) -> str:
    """Replace the model class name in a `get_object_or_404` message with the resource name v2 clients know.

    Django's message names internal classes, such as "No Experiment matches...", which mean nothing to an API
    client. A model with no known name gets a plain "Not found." so the class name is never exposed.
    """
    match = _DJANGO_NOT_FOUND.match(message)
    if match is None:
        return message
    model_name = match.group("model")
    name = _V2_RESOURCE_NAMES.get(model_name) or next(
        (str(model._meta.verbose_name) for model in apps.get_models() if model.__name__ == model_name), None
    )
    return f"No {name} matches the given query." if name else "Not found."
