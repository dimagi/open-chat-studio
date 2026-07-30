from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView
from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

from apps.teams.helpers import get_default_team_from_request
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Team
from apps.teams.utils import set_current_team

from .forms import AuthorizationForm, RegisterApplicationForm, RegisterGlobalApplicationForm
from .models import OAuth2Application
from .tables import GlobalOAuth2ApplicationTable, OAuth2ApplicationTable


class TeamScopedAuthorizationView(BaseAuthorizationView):
    """Authorization view that supports team-scoped OAuth access.

    The team can be specified via the 'team' URL parameter (optional).
    If not provided, defaults to the user's team on the current session.
    """

    form_class = AuthorizationForm
    template_name = "oauth2_provider/authorize.html"

    @cached_property
    def requested_team(self):
        """Return the team requested via URL parameter, or None if not found or the user is not a member."""
        if team_slug := self.request.GET.get("team"):
            try:
                return self.request.user.teams.get(slug=team_slug)
            except Team.DoesNotExist:
                return None
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["requested_team"] = self.requested_team
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["team_requested"] = bool(self.requested_team)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if (team := self.requested_team) or (team := get_default_team_from_request(self.request)):
            team_slug = team.slug
            # If no team found, team_slug remains None and the form will handle it.
        else:
            team_slug = None

        initial["team_slug"] = team_slug
        return initial

    def form_valid(self, form):
        # Set the team as thread context so the validator can pick it up
        set_current_team(Team.objects.get(slug=form.cleaned_data["team_slug"]))
        return super().form_valid(form)


def _manage_team_url(team_slug: str) -> str:
    """The team admin page, anchored on the OAuth applications section it is managed from.

    The anchor is the slugified section title rendered by `generic/object_home_content.html`.
    """
    return f"{reverse('single_team:manage_team', args=[team_slug])}#oauth-applications"


class ApplicationHome(LoginAndTeamRequiredMixin, TemplateView):
    """Home view for the team's OAuth applications."""

    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "manage-team",
            "title": "OAuth Applications",
            "new_object_url": reverse("oauth_apps:new", args=[self.request.team.slug]),
            "table_url": reverse("oauth_apps:table", args=[self.request.team.slug]),
            "enable_search": False,
        }


class ApplicationTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    """List view for the OAuth applications registered to the current team."""

    model = OAuth2Application
    table_class = OAuth2ApplicationTable
    template_name = "table/single_table.html"
    permission_required = "oauth.view_oauth2application"

    def get_queryset(self):
        return OAuth2Application.objects.filter(team=self.request.team).order_by("-created")


class CreateApplication(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    """Register a new OAuth application for the current team."""

    model = OAuth2Application
    form_class = RegisterApplicationForm
    template_name = "oauth2_provider/application_form.html"
    permission_required = "oauth.add_oauth2application"
    extra_context = {
        "active_tab": "manage-team",
        "title": "Register New Application",
        "button_text": "Register",
    }

    def get_initial(self):
        return {
            "authorization_grant_type": OAuth2Application.GRANT_AUTHORIZATION_CODE,
            "algorithm": OAuth2Application.RS256_ALGORITHM,
        }

    def get_success_url(self):
        return _manage_team_url(self.request.team.slug)

    def form_valid(self, form):
        form.instance.user = self.request.user
        # Every token this application issues is scoped to its team, so it is taken from the URL rather
        # than the form: see RegisterApplicationForm.
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditApplication(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    """Update an OAuth application belonging to the current team."""

    model = OAuth2Application
    form_class = RegisterApplicationForm
    template_name = "oauth2_provider/application_form.html"
    permission_required = "oauth.change_oauth2application"
    extra_context = {
        "active_tab": "manage-team",
        "title": "Update Application",
        "button_text": "Update",
    }

    def get_queryset(self):
        return OAuth2Application.objects.filter(team=self.request.team)

    def get_success_url(self):
        return _manage_team_url(self.request.team.slug)


class DeleteApplication(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Delete an OAuth application belonging to the current team."""

    permission_required = "oauth.delete_oauth2application"

    def delete(self, request, team_slug: str, pk: int):
        application = get_object_or_404(OAuth2Application, id=pk, team=request.team)
        application.delete()
        messages.success(request, "Application deleted")
        return HttpResponse()


class SuperuserRequiredMixin(UserPassesTestMixin):
    """Restrict a view to superusers, hiding its existence from everyone else."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser

    def handle_no_permission(self):
        # Don't reveal that the page exists, matching the site admin area (apps/admin/views.py, which
        # sends everyone else to /404).
        raise Http404


class GlobalApplicationHome(SuperuserRequiredMixin, TemplateView):
    """Home view for global (team-less) OAuth applications."""

    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "title": "Global OAuth Applications",
            "subtitle": (
                "Applications that are not registered to a team. The authorizing user chooses which of "
                "their teams the token is scoped to."
            ),
            "new_object_url": reverse("oauth2_provider:global_application_new"),
            "table_url": reverse("oauth2_provider:global_application_table"),
            "enable_search": False,
        }


class GlobalApplicationTableView(SuperuserRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    """List view for global (team-less) OAuth applications."""

    model = OAuth2Application
    table_class = GlobalOAuth2ApplicationTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return OAuth2Application.objects.filter(team__isnull=True).order_by("-created")


class CreateGlobalApplication(SuperuserRequiredMixin, CreateView):
    """Register a new global OAuth application."""

    model = OAuth2Application
    form_class = RegisterGlobalApplicationForm
    template_name = "oauth2_provider/application_form.html"
    success_url = reverse_lazy("oauth2_provider:global_application_home")
    extra_context = {
        "title": "Register New Global Application",
        "button_text": "Register",
    }

    def get_initial(self):
        return {
            "authorization_grant_type": OAuth2Application.GRANT_AUTHORIZATION_CODE,
            "algorithm": OAuth2Application.RS256_ALGORITHM,
        }

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.team = None
        return super().form_valid(form)


class EditGlobalApplication(SuperuserRequiredMixin, UpdateView):
    """Update a global OAuth application."""

    model = OAuth2Application
    form_class = RegisterGlobalApplicationForm
    template_name = "oauth2_provider/application_form.html"
    success_url = reverse_lazy("oauth2_provider:global_application_home")
    extra_context = {
        "title": "Update Global Application",
        "button_text": "Update",
    }

    def get_queryset(self):
        return OAuth2Application.objects.filter(team__isnull=True)


class DeleteGlobalApplication(SuperuserRequiredMixin, View):
    """Delete a global OAuth application."""

    def delete(self, request, pk: int):
        application = get_object_or_404(OAuth2Application, id=pk, team__isnull=True)
        application.delete()
        messages.success(request, "Application deleted")
        return HttpResponse()
