"""DRF exception handlers for the API (#2140 / #2349).

Kept out of apps.api.throttling: importing rest_framework.views evaluates the
APIView class body, which resolves DEFAULT_THROTTLE_CLASSES and so imports the
throttling module. EXCEPTION_HANDLER is resolved per request instead, so this
module can import rest_framework.views at module level without a cycle.
"""

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        return Response(
            {"detail": f"Request body too large. The maximum is {settings.DATA_UPLOAD_MAX_MEMORY_SIZE} bytes."},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        response.data = {"detail": "Rate limit exceeded.", "available_in": int(exc.wait or 0)}
    return response
