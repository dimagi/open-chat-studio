import json
import re

from celery.result import GroupResult
from celery_progress.backend import GroupProgress
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from health_check.views import HealthCheckView

from apps.teams.decorators import check_superuser_team_access, login_and_team_required
from apps.teams.models import Membership, Team
from apps.teams.roles import is_member
from apps.web.elevation import Elevation, Grant, GrantKind, InvalidGrant, TooManyElevations
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


class ConfirmIdentityForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    redirect = forms.CharField(widget=forms.HiddenInput, required=False)


def _safe_redirect(url: str) -> str:
    """Where to send the user once they are done here, falling back to the site root."""
    if not url or not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return "/"
    return url


def _grant_on_confirmed_identity(request, grant, form):
    """Elevate to `grant` if the submitted password checks out.

    Returns the redirect to follow, or None having populated the form's errors.
    """
    if not request.user.check_password(form.cleaned_data["password"]):
        form.add_error("password", "Invalid password")
        return None

    try:
        Elevation(request).add(grant)
    except TooManyElevations:
        form.add_error(
            None,
            "You already hold the maximum number of elevated privileges. Release one of them and try again.",
        )
        return None

    return HttpResponseRedirect(_safe_redirect(form.cleaned_data["redirect"]))


@login_required
@sensitive_post_parameters()
def elevate_django_admin(request):
    return _acquire_elevation(request, Grant.DJANGO_ADMIN)


@login_required
@sensitive_post_parameters()
def elevate_ocs_admin(request):
    return _acquire_elevation(request, Grant.OCS_ADMIN)


@login_required
@sensitive_post_parameters()
def elevate_team(request, team_slug):
    if not Team.objects.filter(slug=team_slug).exists():
        raise Http404
    return _acquire_elevation(request, Grant.team(team_slug))


def _acquire_elevation(request, grant):
    if not grant.may_be_held_by(request.user):
        # Don't leak which surfaces exist to someone who could never elevate into them.
        raise Http404

    if request.method == "POST":
        form = ConfirmIdentityForm(request.POST)
        if form.is_valid():
            if response := _grant_on_confirmed_identity(request, grant, form):
                return response
    else:
        redirect_to = _safe_redirect(request.GET.get("next", ""))
        if grant.kind is GrantKind.TEAM and _is_team_member(request.user, grant.team_slug):
            return HttpResponseRedirect(redirect_to)

        form = ConfirmIdentityForm(initial={"redirect": redirect_to})

    return render(request, "web/temporary_superuser_powers.html", {"form": form, "grant": grant})


def _is_team_member(user, team_slug):
    return Membership.objects.filter(team__slug=team_slug, user=user).exists()


@login_required
def release_elevation(request, grant):
    try:
        parsed = Grant.parse(grant)
    except InvalidGrant:
        raise Http404 from None

    Elevation(request).drop(parsed)

    return HttpResponseRedirect(_safe_redirect(request.GET.get("next", "")))


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
