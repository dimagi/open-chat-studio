from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from apps.teams.helpers import get_team_for_request, get_team_membership_for_request
from apps.teams.models import Membership, Team
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


def _can_see_pending_deletion_page(user, team) -> bool:
    if not user.is_authenticated:
        return False
    return user.is_superuser or Membership.objects.filter(team=team, user=user).exists()


class TeamsMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if team_slug := view_kwargs.get("team_slug"):
            team = Team.objects.filter(slug=team_slug).first()
            if team is not None and team.is_pending_deletion:
                if _can_see_pending_deletion_page(request.user, team):
                    url = reverse("teams:pending_deletion", args=[team.slug])
                    if getattr(request, "htmx", False):
                        return HttpResponse(headers={"HX-Redirect": url})
                    return HttpResponseRedirect(url)
                # Everyone else sees the team exactly as if it did not exist.
                team = None
            if team is not None:
                request.session["team"] = team.id
            request._cached_team = team

        request.team = SimpleLazyObject(lambda: _get_team(request, view_kwargs))
        request.team_membership = SimpleLazyObject(lambda: _get_team_membership(request))

        # this is unset by the request_finished signal
        set_current_team(request.team)
        return None
