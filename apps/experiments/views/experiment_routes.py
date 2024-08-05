from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, UpdateView

from apps.experiments.forms import EXPERIMENT_ROUTE_TYPE_FORMS
from apps.experiments.models import Experiment, ExperimentRoute, ExperimentRouteType
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CreateExperimentRoute(CreateView):
    model = ExperimentRoute
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Child Route",
        "button_text": "Create",
    }

    def get_form(self, form_class=None):
        form_class = EXPERIMENT_ROUTE_TYPE_FORMS[self.kwargs["type"]]
        form = super().get_form(form_class)
        experiment = get_object_or_404(Experiment, id=self.kwargs["experiment_id"])
        form.fields["child"].queryset = ExperimentRoute.eligible_children(self.request.team, parent=experiment)
        return form

    def get_success_url(self):
        url = reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.kwargs["experiment_id"]])
        tab = "routes" if self.kwargs["type"] == "processor" else "post_processor"
        return f"{url}#{tab}"

    def form_valid(self, form):
        form.instance.team = self.request.team
        self.object = form.save(commit=False)
        self.object.parent_id = self.kwargs["experiment_id"]
        self.object.type = ExperimentRouteType(self.kwargs["type"])
        self.object.save()
        messages.success(self.request, "Experiment Route created")
        return super().form_valid(form)


class EditExperimentRoute(UpdateView):
    model = ExperimentRoute
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update experiment routes",
        "button_text": "Update",
    }

    def get_form(self, form_class=None):
        form_class = EXPERIMENT_ROUTE_TYPE_FORMS[self.object.type]
        form = super().get_form(form_class)
        experiment = get_object_or_404(Experiment, id=self.kwargs["experiment_id"])
        eligible_children = ExperimentRoute.eligible_children(self.request.team, parent=experiment)
        form.fields["child"].queryset = eligible_children | Experiment.objects.filter(id=self.object.child_id)
        return form

    def get_success_url(self):
        url = reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.kwargs["experiment_id"]])
        return f"{url}#routes"


class DeleteExperimentRoute(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int, experiment_id: int):
        experiment_route = get_object_or_404(ExperimentRoute, id=pk, parent_id=experiment_id, team=request.team)
        experiment_route.delete()
        messages.success(request, "Experiment Route Deleted")
        return HttpResponse()
