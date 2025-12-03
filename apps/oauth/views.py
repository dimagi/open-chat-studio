from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView
from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

from apps.teams.helpers import get_default_team_from_request
from apps.teams.models import Team
from apps.teams.utils import set_current_team

from .forms import AuthorizationForm, RegisterApplicationForm
from .models import OAuth2Application
from .tables import OAuth2ApplicationTable


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


class ApplicationOwnerIsUserMixin(LoginRequiredMixin):
    """Mixin to ensure users can only access their own OAuth applications."""

    def get_queryset(self):
        return OAuth2Application.objects.filter(user=self.request.user)


class ApplicationHome(LoginRequiredMixin, TemplateView):
    """Home view for OAuth applications."""

    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "title": "OAuth Applications",
            "new_object_url": reverse("oauth2_provider:application_new"),
            "table_url": reverse("oauth2_provider:application_table"),
            "enable_search": False,
        }


class ApplicationTableView(LoginRequiredMixin, PermissionRequiredMixin, SingleTableView):
    """List view for all OAuth applications owned by the current user using django-tables2."""

    model = OAuth2Application
    table_class = OAuth2ApplicationTable
    template_name = "table/single_table.html"
    permission_required = "oauth.view_oauth2application"

    def get_queryset(self):
        return OAuth2Application.objects.filter(user=self.request.user).order_by("-created")


class CreateApplication(LoginRequiredMixin, CreateView):
    """Create view for registering a new OAuth application with restricted fields."""

    model = OAuth2Application
    form_class = RegisterApplicationForm
    template_name = "oauth2_provider/application_form.html"
    success_url = reverse_lazy("oauth2_provider:application_home")
    extra_context = {
        "title": "Register New Application",
        "button_text": "Register",
    }

    def get_initial(self):
        return {
            "authorization_grant_type": OAuth2Application.GRANT_AUTHORIZATION_CODE,
            "algorithm": OAuth2Application.RS256_ALGORITHM,
        }

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)


class EditApplication(ApplicationOwnerIsUserMixin, UpdateView):
    """Update view for an OAuth application owned by the current user."""

    model = OAuth2Application
    form_class = RegisterApplicationForm
    template_name = "oauth2_provider/application_form.html"
    success_url = reverse_lazy("oauth2_provider:application_home")
    extra_context = {
        "title": "Update Application",
        "button_text": "Update",
    }

    def get_queryset(self):
        return OAuth2Application.objects.filter(user=self.request.user)


class DeleteApplication(LoginRequiredMixin, View):
    """Delete view for an OAuth application owned by the current user."""

    def delete(self, request, pk: int):
        application = get_object_or_404(OAuth2Application, id=pk, user=request.user)
        application.delete()
        messages.success(request, "Application deleted")
        return HttpResponse()
