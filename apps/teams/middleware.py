from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from apps.teams.helpers import get_team_for_request, get_team_membership_for_request
from apps.teams.models import Team
from apps.teams.utils import set_current_team


def _get_team(request, view_kwargs):
    if not hasattr(request, "_cached_team"):
        team = get_team_for_request(request, view_kwargs)
        if team:
            request.session["team"] = team.id
        request._cached_team = team
    return request._cached_team


def _get_team_membership(request):
    if not hasattr(request, "_cached_team_membership"):
        request._cached_team_membership = get_team_membership_for_request(request)
    return request._cached_team_membership


def _pending_deletion_response(request, team_slug):
    """Send members of a team that is scheduled for deletion to the page that explains it."""
    if not Team.objects.filter(slug=team_slug, deletion_requested_at__isnull=False).exists():
        return None
    if not request.user.is_authenticated:
        raise Http404
    url = reverse("teams:pending_deletion", args=[team_slug])
    if getattr(request, "htmx", False):
        return HttpResponse(headers={"HX-Redirect": url})
    return HttpResponseRedirect(url)


class TeamsMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if team_slug := view_kwargs.get("team_slug"):
            if response := _pending_deletion_response(request, team_slug):
                return response

        request.team = SimpleLazyObject(lambda: _get_team(request, view_kwargs))
        request.team_membership = SimpleLazyObject(lambda: _get_team_membership(request))

        # this is unset by the request_finished signal
        set_current_team(request.team)
        return None
