from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView
from oauthlib.oauth2 import AccessDeniedError

from apps.teams.helpers import get_default_team_from_request
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Team
from apps.teams.utils import set_current_team

from .forms import AuthorizationForm, RegisterApplicationForm, RegisterGlobalApplicationForm
from .models import OAuth2Application, manage_applications_url
from .tables import GlobalOAuth2ApplicationTable, OAuth2ApplicationTable


class TeamScopedAuthorizationView(BaseAuthorizationView):
    """Authorization view that scopes the granted token to a team.

    An application registered to a team always authorizes against that team, and only members of it may
    do so. A global application has no team of its own, so the authorizing user picks one: either the
    team named by the 'team' URL parameter, or the team on the current session.
    """

    form_class = AuthorizationForm
    template_name = "oauth2_provider/authorize.html"

    @cached_property
    def application(self):
        """The application being authorized, resolved from the client_id on the request."""
        client_id = self.request.POST.get("client_id") or self.request.GET.get("client_id")
        if not client_id:
            return None
        return OAuth2Application.objects.filter(client_id=client_id).select_related("team").first()

    @cached_property
    def application_team(self):
        """The team the application is registered to, or None if it is global."""
        return self.application.team if self.application else None

    @cached_property
    def requested_team(self):
        """Return the team requested via URL parameter, or None if not found or the user is not a member."""
        if team_slug := self.request.GET.get("team"):
            try:
                return self.request.user.teams.get(slug=team_slug)
            except Team.DoesNotExist:
                return None
        return None

    def get(self, request, *args, **kwargs):
        team = self.application_team
        if team and not request.user.teams.filter(id=team.id).exists():
            return self._refuse_non_member(request, team)
        if not team and self.requested_team:
            # The paths that skip the authorization form (`skip_authorization`, `approval_prompt=auto`)
            # never reach `form_valid`, so pin the requested team here too or the grant would be scoped
            # to the team on the session instead of the one that was asked for.
            set_current_team(self.requested_team)
        return super().get(request, *args, **kwargs)

    def _refuse_non_member(self, request, team):
        """Refuse to authorize, since the token would be scoped to a team the user has no access to.

        `AuthorizationForm.clean_team_slug` rejects the POST; this covers the GET.
        """
        description = gettext("You are not a member of the %(team)s team.") % {"team": team.name}
        if request.GET.get("prompt") != "none":
            # Interactive request: show the reason. Redirecting back to the client would bounce the user
            # somewhere that cannot explain why they were refused.
            return self.render_to_response({"error": {"error": "access_denied", "description": description}})

        # Silent authentication runs in a hidden iframe, so an HTML page is never seen: the relying party
        # expects `error=access_denied` at its redirect URI (OIDC 3.1.2.6). Validate the request first so
        # the URI redirected to is one registered on the application, not whatever the query string asked
        # for -- otherwise this would be an open redirect.
        try:
            _scopes, credentials = self.validate_authorization_request(request)
        except OAuthToolkitError as error:
            return self.error_response(error, application=None)
        error = OAuthToolkitError(
            error=AccessDeniedError(description=description, state=credentials.get("state")),
            redirect_uri=credentials["redirect_uri"],
        )
        return self.error_response(error, application=self.application)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["application_team"] = self.application_team
        context["requested_team"] = self.requested_team
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["application_team"] = self.application_team
        kwargs["team_requested"] = bool(self.requested_team)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        team = self.application_team or self.requested_team or get_default_team_from_request(self.request)
        initial["team_slug"] = team.slug if team else None
        return initial

    def form_valid(self, form):
        # Set the team as thread context so the validator can pick it up
        set_current_team(Team.objects.get(slug=form.cleaned_data["team_slug"]))
        return super().form_valid(form)


class TeamApplicationBreadcrumbsMixin:
    """Name the list this form was reached from.

    The team-scoped and global application forms share a template but live in different URL
    spaces, so each side supplies its own parent rather than the template guessing from a `team`
    that a create view has no object to read it off.
    """

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            "breadcrumb_parent_url": manage_applications_url(self.request.team.slug),
            "breadcrumb_parent_label": "OAuth Applications",
        }


class ApplicationHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Home view for the team's OAuth applications."""

    template_name = "generic/object_home.html"
    permission_required = "oauth.view_oauth2application"

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


class CreateApplication(
    TeamApplicationBreadcrumbsMixin, LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView
):
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

    def get_form_kwargs(self):
        # The chatbots on offer are the team's, and `form_valid` sets the team too late to filter a
        # queryset with.
        return super().get_form_kwargs() | {"team": self.request.team}

    def get_success_url(self):
        return manage_applications_url(self.request.team.slug)

    def form_valid(self, form):
        form.instance.user = self.request.user
        # Every token this application issues is scoped to its team, so it is taken from the URL rather
        # than the form: see RegisterApplicationForm.
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditApplication(TeamApplicationBreadcrumbsMixin, LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
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

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"team": self.request.team}

    def get_success_url(self):
        return manage_applications_url(self.request.team.slug)


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
        "breadcrumb_parent_url": reverse_lazy("oauth2_provider:global_application_home"),
        "breadcrumb_parent_label": "Global OAuth Applications",
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
        "breadcrumb_parent_url": reverse_lazy("oauth2_provider:global_application_home"),
        "breadcrumb_parent_label": "Global OAuth Applications",
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
