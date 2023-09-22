from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse

from .roles import is_admin, is_member


def login_and_team_required(view_func):
    return _get_decorated_function(view_func, is_member)


def team_admin_required(view_func):
    return _get_decorated_function(view_func, is_admin)


def _get_decorated_function(view_func, permission_test_function):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseRedirect("{}?next={}".format(reverse("account_login"), request.path))

        team = request.team  # set by middleware
        if not team or not permission_test_function(user, team):
            # treat not having access to a team like a 404 to avoid accidentally leaking information
            raise Http404

        return view_func(request, *args, **kwargs)

    return _inner
