from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from health_check.views import MainView

from apps.teams.decorators import login_and_team_required
from apps.teams.models import Membership
from apps.teams.superuser_utils import apply_temporary_superuser_access, remove_temporary_superuser_access


def home(request):
    if request.user.is_authenticated:
        team = request.team
        if team:
            return HttpResponseRedirect(reverse("experiments:experiments_home", args=[team.slug]))
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
    assert request.team.slug == team_slug
    return render(
        request,
        "web/app_home.html",
        context={
            "team": request.team,
            "active_tab": "dashboard",
            "page_title": _("{team} Dashboard").format(team=request.team),
        },
    )


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
def acquire_superuser_powers(request, team_slug):
    if not request.team:
        raise Http404

    if request.method == "POST":
        form = ConfirmIdentityForm(request.POST)
        if form.is_valid():
            if not request.user.check_password(form.cleaned_data["password"]):
                form.add_error("password", "Invalid password")
            else:
                apply_temporary_superuser_access(request, team_slug)
                redirect_to = form.cleaned_data["redirect"] or "/"
                return HttpResponseRedirect(redirect_to or "/")
    else:
        redirect_to = request.GET.get("next", "")
        if Membership.objects.filter(team=request.team, user=request.user).exists():
            return HttpResponseRedirect(redirect_to or "/")

        form = ConfirmIdentityForm(initial={"redirect": redirect_to})

    return render(
        request,
        "teams/temporary_superuser_powers.html",
        {
            "team": request.team,
            "form": form,
        },
    )


@user_passes_test(lambda u: u.is_superuser)
def release_superuser_powers(request, team_slug):
    if not request.team:
        raise Http404

    remove_temporary_superuser_access(request, team_slug)

    return HttpResponseRedirect("/")
