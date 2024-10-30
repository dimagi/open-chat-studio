from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView, tables

from apps.custom_actions.fields import JSONORYAMLField
from apps.custom_actions.models import CustomAction
from apps.generics import actions
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CustomActionForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": "3"}), required=False)
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={"rows": "3"}),
        required=False,
        label="Additional Prompt",
        help_text="Use this field to provide additional instructions to the LLM",
    )
    api_schema = JSONORYAMLField(
        widget=forms.Textarea(attrs={"rows": "10"}),
        required=True,
        label="API Schema",
        help_text="Paste in the OpenAPI schema for the API you want to interact with. "
        "This will be used to generate the API calls for the LLM. Accepts YAML or JSON.",
        initial={},
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description", "prompt", "api_schema")


class CustomActionTable(tables.Table):
    name = tables.columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "custom_actions:edit",
                required_permissions=["custom_actions.change_customaction"],
            ),
            actions.delete_action(
                "custom_actions:delete",
                required_permissions=["custom_actions.delete_customaction"],
            ),
        ]
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No actions found."


class CustomActionHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "custom_actions",
            "title": "Custom Actions",
            # "info_link": settings.DOCUMENTATION_LINKS["consent"],
            "new_object_url": reverse("custom_actions:new", args=[team_slug]),
            "table_url": reverse("custom_actions:table", args=[team_slug]),
        }


class CustomActionTableView(SingleTableView):
    model = CustomAction
    paginate_by = 25
    table_class = CustomActionTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)


class CreateCustomAction(CreateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Custom Action",
        "button_text": "Create",
        "active_tab": "custom_actions",
    }

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditCustomAction(UpdateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Custom Action",
        "button_text": "Update",
        "active_tab": "custom_actions",
    }

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])


class DeleteCustomAction(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        consent_form = get_object_or_404(CustomAction, id=pk, team=request.team)
        consent_form.delete()
        messages.success(request, "Custom Action Deleted")
        return HttpResponse()
