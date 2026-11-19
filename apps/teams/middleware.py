from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject
from django_htmx.http import HttpResponseClientRedirect

from apps.teams.decorators import TeamAccessDenied
from apps.teams.helpers import get_pending_team_access, get_team_for_request, get_team_membership_for_request
from apps.teams.utils import set_current_team


def _get_team(request, view_kwargs):
    if not hasattr(request, "_cached_team"):
        _cache_team(request, get_team_for_request(request, view_kwargs))
    return request._cached_team


def _cache_team(request, team):
    if team:
        request.session["team"] = team.id
    request._cached_team = team


def _pending_deletion_response(request, team):
    """Redirect those who may see a team pending deletion to its page; None for everyone else."""
    can_view, _ = get_pending_team_access(request, team)
    if can_view:
        url = reverse("teams:pending_deletion", args=[team.slug])
        if getattr(request, "htmx", False):
            return HttpResponseClientRedirect(url)
        return HttpResponseRedirect(url)
    if request.user.is_superuser:
        # The 404 page offers an unelevated superuser the team's elevation grant.
        raise TeamAccessDenied
    return None


def _get_team_membership(request):
    if not hasattr(request, "_cached_team_membership"):
        request._cached_team_membership = get_team_membership_for_request(request)
    return request._cached_team_membership


class TeamsMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if view_kwargs.get("team_slug"):
            team = get_team_for_request(request, view_kwargs)
            if team is not None and team.is_pending_deletion:
                if response := _pending_deletion_response(request, team):
                    return response
                # Everyone else sees the team exactly as if it did not exist.
                team = None
            _cache_team(request, team)

        request.team = SimpleLazyObject(lambda: _get_team(request, view_kwargs))
        request.team_membership = SimpleLazyObject(lambda: _get_team_membership(request))

        # this is unset by the request_finished signal
        set_current_team(request.team)
        return None
