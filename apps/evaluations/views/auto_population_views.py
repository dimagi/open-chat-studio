from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, UpdateView
from waffle import flag_is_active

from apps.evaluations.forms import DatasetAutoPopulationRuleForm
from apps.evaluations.models import DatasetAutoPopulationRule, EvaluationDataset
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin


class FlagGatedMixin:
    """Raise 404 unless the auto-populate flag is active for the request's team."""

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, Flags.AUTO_POPULATE_EVAL_DATASETS.slug):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class _DatasetScopedMixin:
    """Resolve the parent dataset from the URL and enforce team scoping."""

    def get_dataset(self) -> EvaluationDataset:
        return get_object_or_404(
            EvaluationDataset,
            pk=self.kwargs["dataset_id"],
            team=self.request.team,
        )


class CreateAutoPopulationRule(
    FlagGatedMixin,
    LoginAndTeamRequiredMixin,
    PermissionRequiredMixin,
    _DatasetScopedMixin,
    CreateView,
):
    permission_required = "evaluations.add_datasetautopopulationrule"
    model = DatasetAutoPopulationRule
    form_class = DatasetAutoPopulationRuleForm
    template_name = "evaluations/auto_population_rule_form.html"

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "team": self.request.team,
            "dataset": self.get_dataset(),
        }

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "dataset": self.get_dataset(),
            "title": "Create Auto-Population Rule",
            "page_title": "Create Auto-Population Rule",
            "button_text": "Create Rule",
            "active_tab": "evaluation_datasets",
        }

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule created.")
        return response

    def get_success_url(self):
        return reverse(
            "evaluations:dataset_edit",
            args=[self.request.team.slug, self.kwargs["dataset_id"]],
        )


class EditAutoPopulationRule(
    FlagGatedMixin,
    LoginAndTeamRequiredMixin,
    PermissionRequiredMixin,
    _DatasetScopedMixin,
    UpdateView,
):
    permission_required = "evaluations.change_datasetautopopulationrule"
    model = DatasetAutoPopulationRule
    form_class = DatasetAutoPopulationRuleForm
    template_name = "evaluations/auto_population_rule_form.html"

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(
            team=self.request.team,
            dataset_id=self.kwargs["dataset_id"],
        )

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "team": self.request.team,
            "dataset": self.get_dataset(),
        }

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "dataset": self.get_dataset(),
            "title": "Edit Auto-Population Rule",
            "page_title": "Edit Auto-Population Rule",
            "button_text": "Update Rule",
            "active_tab": "evaluation_datasets",
        }

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule updated.")
        return response

    def get_success_url(self):
        return reverse(
            "evaluations:dataset_edit",
            args=[self.request.team.slug, self.kwargs["dataset_id"]],
        )


class DeleteAutoPopulationRule(
    FlagGatedMixin,
    LoginAndTeamRequiredMixin,
    PermissionRequiredMixin,
    DeleteView,
):
    permission_required = "evaluations.delete_datasetautopopulationrule"
    model = DatasetAutoPopulationRule

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(
            team=self.request.team,
            dataset_id=self.kwargs["dataset_id"],
        )

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return HttpResponse(status=200)
