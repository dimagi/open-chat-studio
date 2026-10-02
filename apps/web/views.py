import json
import re

from celery.result import GroupResult
from celery_progress.backend import GroupProgress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from health_check.views import HealthCheckView

from apps.teams.decorators import check_superuser_team_access, login_and_team_required
from apps.teams.models import Membership, Team
from apps.teams.roles import is_member
from apps.web.elevation import (
    TOO_MANY_ELEVATIONS_MESSAGE,
    Elevation,
    Grant,
    GrantKind,
    InvalidGrant,
    safe_redirect_url,
    start_elevation,
)
from apps.web.health_checks import CHECK_SUBSETS
from apps.web.search import get_searchable_models

UUID_PATTERN = re.compile(r"^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$", re.IGNORECASE)


@login_and_team_required
def team_home(request, team_slug):
    return redirect("dashboard:index", team_slug=request.team.slug)


class HealthCheck(HealthCheckView):
    checks = CHECK_SUBSETS["general"]

    async def get(self, request, *args, subset=None, **kwargs):
        tokens = settings.HEALTH_CHECK_TOKENS
        if tokens and request.GET.get("token") not in tokens:
            raise Http404
        if subset is not None:
            try:
                self.checks = CHECK_SUBSETS[subset]
            except KeyError:
                raise Http404 from None
        return await super().get(request, *args, **kwargs)


@login_required
def elevate_django_admin(request):
    return _acquire_elevation(request, Grant.DJANGO_ADMIN)


@login_required
def elevate_ocs_admin(request):
    return _acquire_elevation(request, Grant.OCS_ADMIN)


@login_required
def elevate_team(request, team_slug):
    if not Team.objects.filter(slug=team_slug).exists():
        raise Http404
    return _acquire_elevation(request, Grant.team(team_slug))


def _acquire_elevation(request, grant):
    if not grant.may_be_held_by(request.user):
        # Don't leak which surfaces exist to someone who could never elevate into them.
        raise Http404

    next_url = safe_redirect_url(request.GET.get("next", ""))
    elevation = Elevation(request)
    if elevation.has(grant):
        return HttpResponseRedirect(next_url)

    if grant.kind is GrantKind.TEAM and _is_team_member(request.user, grant.team_slug):
        # A member of the team has no need to stand in for one.
        return HttpResponseRedirect(next_url)

    if elevation.is_full():
        # `next_url` is the page that sent them here, so it would send them straight back.
        messages.error(request, TOO_MANY_ELEVATIONS_MESSAGE)
        return HttpResponseRedirect("/")

    return start_elevation(request, grant, next_url)


def _is_team_member(user, team_slug):
    return Membership.objects.filter(team__slug=team_slug, user=user).exists()


@login_required
def release_elevation(request, grant):
    try:
        parsed = Grant.parse(grant)
    except InvalidGrant:
        raise Http404 from None

    Elevation(request).drop(parsed)

    return HttpResponseRedirect(safe_redirect_url(request.GET.get("next", "")))


@login_required
def global_search(request):
    query = request.GET.get("q", "")
    model = request.GET.get("m")
    if not query:
        return HttpResponseBadRequest("No query provided")

    if not query.isdigit() and not UUID_PATTERN.match(query):
        return HttpResponseBadRequest("Only UUID and Int searches are supported")

    for candidate in get_searchable_models(model):
        if result := candidate.search(query):
            team = result.team
            if not is_member(request.user, team):
                check_superuser_team_access(request, team.slug)

            if not request.user.has_perm(candidate.permission):
                raise Http404

            return HttpResponseRedirect(result.get_absolute_url())

    raise Http404


@never_cache
@login_required
def celery_task_group_status(request, group_id):
    group_result = GroupResult.restore(group_id)
    if group_result:
        group_progress = GroupProgress(group_result).get_info()
    else:
        group_progress = {
            "complete": False,
            "success": False,
            "progress": {
                "pending": True,
                "total": 0,
                "current": 0,
                "percent": 0,
            },
        }
    return HttpResponse(json.dumps(group_progress), content_type="application/json")
