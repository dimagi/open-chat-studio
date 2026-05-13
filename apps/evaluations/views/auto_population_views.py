from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from apps.evaluations.forms import DatasetAutoPopulationRuleForm
from apps.evaluations.models import DatasetAutoPopulationRule, EvaluationDataset, EvaluationMode
from apps.experiments.filters import ChatMessageFilter, ExperimentSessionFilter, get_filter_context_data
from apps.filters.models import FilterSet
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class _RuleViewMixin(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
    permission_required = "evaluations.change_evaluationdataset"
    model = DatasetAutoPopulationRule
    form_class = DatasetAutoPopulationRuleForm
    template_name = "evaluations/auto_population_rule_form.html"

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(team=self.request.team)

    def get_dataset(self):
        return get_object_or_404(EvaluationDataset, id=self.kwargs["dataset_id"], team=self.request.team)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        kwargs["dataset"] = self.get_dataset()
        return kwargs

    def get_success_url(self):
        return reverse(
            "evaluations:dataset_edit",
            args=[self.request.team.slug, self.kwargs["dataset_id"]],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dataset = self.get_dataset()
        team = self.request.team
        if dataset.evaluation_mode == EvaluationMode.SESSION:
            filter_class = ExperimentSessionFilter
        else:
            filter_class = ChatMessageFilter
        context.update(
            get_filter_context_data(
                team,
                filter_class.columns(team),
                filter_class=filter_class,
                table_url=reverse("evaluations:dataset_sessions_selection_list", args=[team.slug]),
                table_container_id="sessions-table",
                table_type=FilterSet.TableType.DATASETS,
            )
        )
        return context


class CreateAutoPopulationRule(_RuleViewMixin, CreateView):
    extra_context = {"page_title": "Add auto-population rule", "button_text": "Create rule"}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule created.")
        return response


class EditAutoPopulationRule(_RuleViewMixin, UpdateView):
    extra_context = {"page_title": "Edit auto-population rule", "button_text": "Save"}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule updated.")
        return response


class DeleteAutoPopulationRule(LoginAndTeamRequiredMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "evaluations.change_evaluationdataset"
    model = DatasetAutoPopulationRule

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse(
            "evaluations:dataset_edit",
            args=[self.request.team.slug, self.get_object().dataset_id],
        )

    def delete(self, request, *args, **kwargs):
        self.get_object().delete()
        return HttpResponse(status=200)


@login_and_team_required
@permission_required("evaluations.change_evaluationdataset")
@require_POST
def toggle_auto_population_rule(request, team_slug: str, pk: int):
    rule = get_object_or_404(DatasetAutoPopulationRule, id=pk, team__slug=team_slug)
    rule.is_enabled = not rule.is_enabled
    if rule.is_enabled:
        rule.consecutive_failure_count = 0
        rule.last_error = ""
    rule.save(update_fields=["is_enabled", "consecutive_failure_count", "last_error"])
    return redirect(reverse("evaluations:dataset_edit", args=[team_slug, rule.dataset_id]))
