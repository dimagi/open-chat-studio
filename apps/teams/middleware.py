from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from apps.teams.helpers import get_team_for_request
from apps.teams.models import Membership


def _get_team(request, view_kwargs):
    if not hasattr(request, "_cached_team"):
        team = get_team_for_request(request, view_kwargs)
        if team:
            request.session["team"] = team.id
        request._cached_team = team
    return request._cached_team


def _get_team_membership(request):
    if not hasattr(request, "_cached_team_membership"):
        team_membership = None
        if request.user.is_authenticated and request.team:
            try:
                team_membership = Membership.objects.get(team=request.team, user=request.user)
            except Membership.DoesNotExist:
                pass
        request._cached_team_membership = team_membership
    return request._cached_team_membership


class TeamsMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        request.team = SimpleLazyObject(lambda: _get_team(request, view_kwargs))
        request.team_membership = SimpleLazyObject(lambda: _get_team_membership(request))
