from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, UpdateView

from apps.experiments.forms import EXPERIMENT_ROUTE_TYPE_FORMS
from apps.experiments.models import Experiment, ExperimentRoute, ExperimentRouteType
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CreateExperimentRoute(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "experiments.add_experimentroute"
    model = ExperimentRoute
    template_name = "generic/object_form.html"
    extra_context = {
        "button_text": "Create",
    }
    route_type_titles = {
        ExperimentRouteType.PROCESSOR: "Create Child Route",
        ExperimentRouteType.TERMINAL: "Add Terminal Bot",
    }

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        self.extra_context["title"] = self.route_type_titles[self.kwargs["type"]]
        return super().get_context_data(**kwargs)

    def get_form(self, form_class=None):
        form_class = EXPERIMENT_ROUTE_TYPE_FORMS[self.kwargs["type"]]
        form = super().get_form(form_class)
        experiment = get_object_or_404(Experiment, id=self.kwargs["experiment_id"])
        form.fields["child"].queryset = ExperimentRoute.eligible_children(self.request.team, parent=experiment)
        return form

    def get_success_url(self):
        url = reverse("chatbots:single_chatbot_home", args=[self.request.team.slug, self.kwargs["experiment_id"]])
        tab = "routes" if self.kwargs["type"] == ExperimentRouteType.PROCESSOR else "terminal_bots"
        return f"{url}#{tab}"

    def form_valid(self, form):
        form.instance.team = self.request.team
        self.object = form.save(commit=False)
        self.object.parent_id = self.kwargs["experiment_id"]
        self.object.type = ExperimentRouteType(self.kwargs["type"])
        self.object.save()
        messages.success(self.request, "Experiment Route created")
        return super().form_valid(form)


class EditExperimentRoute(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "experiments.change_experimentroute"
    model = ExperimentRoute
    template_name = "generic/object_form.html"
    extra_context = {
        "button_text": "Update",
    }
    route_type_titles = {
        ExperimentRouteType.PROCESSOR: "Update Experiment Routes",
        ExperimentRouteType.TERMINAL: "Update Terminal Bot",
    }

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        self.extra_context["title"] = self.route_type_titles[self.object.type]
        return super().get_context_data(**kwargs)

    def get_form(self, form_class=None):
        form_class = EXPERIMENT_ROUTE_TYPE_FORMS[self.object.type]
        form = super().get_form(form_class)
        experiment = get_object_or_404(Experiment, id=self.kwargs["experiment_id"])
        eligible_children = ExperimentRoute.eligible_children(self.request.team, parent=experiment)
        form.fields["child"].queryset = eligible_children | Experiment.objects.filter(id=self.object.child_id)
        return form

    def get_success_url(self):
        url = reverse("chatbots:single_chatbot_home", args=[self.request.team.slug, self.kwargs["experiment_id"]])
        return f"{url}#routes"


class DeleteExperimentRoute(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "experiments.delete_experimentroute"

    def delete(self, request, team_slug: str, pk: int, experiment_id: int):
        experiment_route = get_object_or_404(ExperimentRoute, id=pk, parent_id=experiment_id, team=request.team)
        experiment_route.archive()
        messages.success(request, "Experiment Route Deleted")
        return HttpResponse()
