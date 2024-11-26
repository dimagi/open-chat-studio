from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse

from apps.teams.superuser_utils import has_temporary_superuser_access


class TeamAccessDenied(Http404):
    pass


def login_and_team_required(view_func):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        if not valid_auth_and_membership(request):
            return HttpResponseRedirect("{}?next={}".format(reverse("account_login"), request.path))
        return view_func(request, *args, **kwargs)

    return _inner


def valid_auth_and_membership(request):
    if not request.user.is_authenticated:
        return False

    if not request.team:
        # treat not having access to a team like a 404 to avoid accidentally leaking information
        raise Http404

    if not request.team_membership:
        if request.user.is_superuser and has_temporary_superuser_access(request, request.team.slug):
            return True

        raise TeamAccessDenied

    return True
