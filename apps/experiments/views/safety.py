from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import SafetyLayer
from apps.experiments.tables import SafetyLayerTable
from apps.teams.mixins import LoginAndTeamRequiredMixin


class SafetyLayerHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "safety_layers",
            "title": "Safety Layers",
            "new_object_url": reverse("experiments:safety_new", args=[team_slug]),
            "table_url": reverse("experiments:safety_table", args=[team_slug]),
        }


class SafetyLayerTableView(SingleTableView):
    model = SafetyLayer
    paginate_by = 25
    table_class = SafetyLayerTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return SafetyLayer.objects.filter(team=self.request.team, is_version=False)


class CreateSafetyLayer(CreateView):
    model = SafetyLayer
    fields = [
        "name",
        "prompt_text",
        "messages_to_review",
        "default_response_to_user",
        "prompt_to_bot",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Safety Layer",
        "button_text": "Create",
        "active_tab": "safety_layers",
    }

    def get_success_url(self):
        return reverse("experiments:safety_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditSafetyLayer(UpdateView):
    model = SafetyLayer
    fields = [
        "name",
        "prompt_text",
        "messages_to_review",
        "default_response_to_user",
        "prompt_to_bot",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Safety Layer",
        "button_text": "Update",
        "active_tab": "safety_layers",
    }

    def get_queryset(self):
        return SafetyLayer.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:safety_home", args=[self.request.team.slug])


class DeleteSafetyLayer(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        safety_layer = get_object_or_404(SafetyLayer, id=pk, team=request.team)
        safety_layer.archive()
        messages.success(request, "Safety Layer")
        return HttpResponse()
