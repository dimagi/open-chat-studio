from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.custom_actions.forms import CustomActionForm
from apps.custom_actions.models import CustomAction
from apps.custom_actions.tables import CustomActionTable
from apps.custom_actions.tasks import check_single_custom_action_health
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CustomActionHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "custom_actions",
            "title": "Custom Actions",
            "new_object_url": reverse("custom_actions:new", args=[self.kwargs["team_slug"]]),
            "table_url": reverse("custom_actions:table", args=[self.kwargs["team_slug"]]),
        }


class CustomActionTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = CustomAction
    table_class = CustomActionTable
    template_name = "table/single_table.html"
    permission_required = "custom_actions.view_customaction"

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)


class CreateCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Custom Action",
        "button_text": "Create",
        "active_tab": "custom_actions",
    }
    permission_required = "custom_actions.add_customaction"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        resp = super().form_valid(form)
        self.object.allowed_operations = list(self.object.get_operations_by_id())
        self.object.save(update_fields=["allowed_operations"])
        return resp


class EditCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Custom Action",
        "button_text": "Update",
        "active_tab": "custom_actions",
    }
    permission_required = "custom_actions.change_customaction"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])


class DeleteCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "custom_actions.delete_customaction"

    def delete(self, request, team_slug: str, pk: int):
        consent_form = get_object_or_404(CustomAction, id=pk, team=request.team)
        consent_form.delete()
        messages.success(request, "Custom Action Deleted")
        return HttpResponse()


class CheckCustomActionHealth(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "custom_actions.view_customaction"

    def post(self, request, team_slug: str, pk: int):
        """Trigger an immediate health check for a custom action."""
        action = get_object_or_404(CustomAction, id=pk, team=request.team)

        if not action.health_endpoint:
            return HttpResponse(
                '<span class="text-gray-500">No health endpoint configured</span>',
                content_type="text/html"
            )

        # Trigger the health check task
        check_single_custom_action_health.delay(action.id)

        # Return a loading indicator that will be replaced when the check completes
        return render(
            request,
            "custom_actions/health_check_loading.html",
            {"team_slug": team_slug, "pk": pk}
        )
