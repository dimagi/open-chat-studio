import json
import re

from celery.result import GroupResult
from celery_progress.backend import GroupProgress
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from health_check.views import MainView

from apps.teams.decorators import check_superuser_team_access, login_and_team_required
from apps.teams.models import Membership, Team
from apps.teams.roles import is_member
from apps.web.admin import ADMIN_SLUG
from apps.web.search import get_searchable_models
from apps.web.superuser_utils import apply_temporary_superuser_access, remove_temporary_superuser_access

UUID_PATTERN = re.compile(r"^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$", re.IGNORECASE)


def home(request):
    if request.user.is_authenticated:
        team = request.team
        if team:
            return redirect("dashboard:index", team_slug=team.slug)
        else:
            messages.info(
                request,
                _("You are not a member of any teams. Create a new one to get started."),
            )
            return HttpResponseRedirect(reverse("teams:create_team"))
    else:
        return render(request, "web/landing_page.html")


@login_and_team_required
def team_home(request, team_slug):
    return redirect("dashboard:index", team_slug=request.team.slug)


class HealthCheck(MainView):
    def get(self, request, *args, **kwargs):
        tokens = settings.HEALTH_CHECK_TOKENS
        if tokens and request.GET.get("token") not in tokens:
            raise Http404
        return super().get(request, *args, **kwargs)


class ConfirmIdentityForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    redirect = forms.CharField(widget=forms.HiddenInput, required=False)


@user_passes_test(lambda u: u.is_superuser)
@sensitive_post_parameters()
def acquire_superuser_powers(request, slug):
    is_team_request = slug != ADMIN_SLUG
    if is_team_request and not Team.objects.filter(slug=slug).exists():
        raise Http404

    if request.method == "POST":
        form = ConfirmIdentityForm(request.POST)
        if form.is_valid():
            if not request.user.check_password(form.cleaned_data["password"]):
                form.add_error("password", "Invalid password")
            else:
                apply_temporary_superuser_access(request, slug)
                redirect_to = form.cleaned_data["redirect"]
                if not redirect_to or not url_has_allowed_host_and_scheme(redirect_to, allowed_hosts=None):
                    redirect_to = "/"
                return HttpResponseRedirect(redirect_to)
    else:
        redirect_to = request.GET.get("next", "")
        if not redirect_to or not url_has_allowed_host_and_scheme(redirect_to, allowed_hosts=None):
            redirect_to = "/"
        if is_team_request and Membership.objects.filter(team__slug=slug, user=request.user).exists():
            return HttpResponseRedirect(redirect_to)

        form = ConfirmIdentityForm(initial={"redirect": redirect_to})

    return render(
        request,
        "web/temporary_superuser_powers.html",
        {
            "form": form,
            "is_team_request": is_team_request,
            "team_slug": slug,
        },
    )


@user_passes_test(lambda u: u.is_superuser)
def release_superuser_powers(request, slug):
    if slug != ADMIN_SLUG and not Team.objects.filter(slug=slug).exists():
        raise Http404

    remove_temporary_superuser_access(request, slug)

    return HttpResponseRedirect("/")


@login_required
def global_search(request):
    query = request.GET.get("q")
    if not query:
        return HttpResponseBadRequest("No query provided")

    if not UUID_PATTERN.match(query):
        return HttpResponseBadRequest("Only UUID searches are supported")

    for candidate in get_searchable_models():
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
