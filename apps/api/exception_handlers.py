"""DRF exception handlers for the API (#2140 / #2349).

Kept out of apps.api.throttling: importing rest_framework.views evaluates the
APIView class body, which resolves DEFAULT_THROTTLE_CLASSES and so imports the
throttling module. EXCEPTION_HANDLER is resolved per request instead, so this
module can import rest_framework.views at module level without a cycle.
"""

from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        response.data = {"detail": "Rate limit exceeded.", "available_in": int(exc.wait or 0)}
    return response
