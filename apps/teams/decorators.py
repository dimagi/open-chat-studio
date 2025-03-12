from functools import wraps

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.web.superuser_utils import apply_temporary_superuser_access, has_temporary_superuser_access


class TeamAccessDenied(Http404):
    """'Tagged' 404 that allows us to detect this condition in error handling.

    See 404.html.
    """

    pass


def login_and_team_required(view_func):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        if not valid_auth_and_membership(request):
            next_url = request.get_full_path()
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = '/'
            return HttpResponseRedirect(f"{reverse(settings.LOGIN_URL)}?next={next_url}")
        return view_func(request, *args, **kwargs)

    return _inner


def valid_auth_and_membership(request):
    if not request.user.is_authenticated:
        return False

    if not request.team:
        # treat not having access to a team like a 404 to avoid accidentally leaking information
        raise Http404

    if not request.team_membership:
        return check_superuser_team_access(request, request.team.slug)

    return True


def check_superuser_team_access(request, team_slug):
    if request.user.is_superuser:
        if has_temporary_superuser_access(request, team_slug):
            return True
        if settings.DEBUG:
            # allow superusers to access any team in DEBUG mode
            apply_temporary_superuser_access(request, team_slug)
            return True

    raise TeamAccessDenied
