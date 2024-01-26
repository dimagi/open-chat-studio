from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse

from .roles import is_member


def login_and_team_required(view_func):
    return _get_decorated_function(view_func, is_member)


def _get_decorated_function(view_func, permission_test_function):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        if not valid_auth_and_membership(request.user, request.team):
            return HttpResponseRedirect("{}?next={}".format(reverse("account_login"), request.path))
        return view_func(request, *args, **kwargs)

    return _inner


def valid_auth_and_membership(user, team):
    if not user.is_authenticated:
        return False

    if not team or not is_member(user, team):
        # treat not having access to a team like a 404 to avoid accidentally leaking information
        raise Http404

    return True
