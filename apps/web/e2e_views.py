"""Endpoints the Playwright E2E tests call to create and delete their data in the server under test.

Mounted by config.urls only when DEBUG or CI is on, and they check again on every request: they
create logged-in users, so they must never be reachable on a real deployment.
"""

from functools import wraps

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.web.e2e_data import STATES, create_e2e_data, delete_e2e_data


def debug_or_ci_only(view):
    """Serve the view only while DEBUG or CI is on."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (settings.DEBUG or settings.IS_CI):
            raise Http404("The E2E data endpoints are served only when DEBUG is on or in CI.")
        return view(request, *args, **kwargs)

    return wrapper


@csrf_exempt
@require_POST
@debug_or_ci_only
def create(request):
    state = request.POST.get("state", "team")
    if state not in STATES:
        return HttpResponseBadRequest(f"state must be one of {STATES}")
    return JsonResponse(create_e2e_data(state=state, base_url=request.build_absolute_uri("/").rstrip("/")))


@csrf_exempt
@require_POST
@debug_or_ci_only
def cleanup(request):
    return JsonResponse({"deleted": delete_e2e_data()})


app_name = "e2e_data"
urlpatterns = [
    path("create/", create, name="create"),
    path("cleanup/", cleanup, name="cleanup"),
]
