from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse


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

    if not request.team or not request.team_membership:
        # treat not having access to a team like a 404 to avoid accidentally leaking information
        raise Http404

    return True
