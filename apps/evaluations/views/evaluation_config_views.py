import csv
from functools import cached_property

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView, columns, tables

from apps.evaluations.forms import EvaluationConfigForm, get_experiment_version_choices
from apps.evaluations.models import EvaluationConfig, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tables import EvaluationConfigTable, EvaluationRunTable
from apps.evaluations.utils import get_evaluators_with_schema
from apps.experiments.models import Experiment
from apps.generics import actions
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class EvaluationHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationconfig"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluations",
            "title": "Evaluations",
            "new_object_url": reverse("evaluations:new", args=[team_slug]),
            "table_url": reverse("evaluations:table", args=[team_slug]),
            # "title_help_content": render_help_with_link(
            #     "Pipelines allow you to create more complex bots by combining one or more steps together.", "pipelines"  # noqa
            # ),
        }


class EvaluationTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationconfig"
    model = EvaluationConfig
    paginate_by = 25
    table_class = EvaluationConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return EvaluationConfig.objects.filter(team=self.request.team).order_by("-created_at")


class CreateEvaluation(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "evaluations.add_evaluationconfig"
    template_name = "evaluations/evaluation_config_form.html"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    extra_context = {
        "title": "Create Evaluation",
        "button_text": "Create",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["available_evaluators"] = get_evaluators_with_schema(self.request.team)
        return context

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditEvaluation(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "evaluations.change_evaluationconfig"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    template_name = "evaluations/evaluation_config_form.html"
    extra_context = {
        "title": "Update Evaluation",
        "button_text": "Update",
        "active_tab": "evaluations",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["available_evaluators"] = get_evaluators_with_schema(self.request.team)
        return context

    def get_queryset(self):
        return EvaluationConfig.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:home", args=[self.request.team.slug])


class EvaluationRunHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluations",
            "title": "Evaluation Runs",
            "allow_new": False,
            "table_url": reverse("evaluations:evaluation_runs_table", args=[team_slug, kwargs["evaluation_pk"]]),
        }


class EvaluationRunTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    model = EvaluationRun
    paginate_by = 25
    table_class = EvaluationRunTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return EvaluationRun.objects.filter(
            config_id=self.kwargs["evaluation_pk"], type=EvaluationRunType.FULL
        ).order_by("-created_at")


class EvaluationResultHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/evaluation_result_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        evaluation_run = get_object_or_404(
            EvaluationRun, id=kwargs["evaluation_run_pk"], config_id=kwargs["evaluation_pk"], team__slug=team_slug
        )

        context = {
            "active_tab": "evaluations",
            "title": (
                "Evaluation Run Preview"
                if evaluation_run.type == EvaluationRunType.PREVIEW
                else "Evaluation Run Results"
            ),
            "evaluation_run": evaluation_run,
            "allow_new": False,
        }

        # Show progress if running, otherwise show results table
        if evaluation_run.status in [EvaluationRunStatus.PROCESSING]:
            context["group_job_id"] = evaluation_run.job_id
        else:
            context["table_url"] = reverse(
                "evaluations:evaluation_results_table",
                args=[team_slug, kwargs["evaluation_pk"], kwargs["evaluation_run_pk"]],
            )

        return context


class EvaluationResultTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "table/single_table.html"

    def get_queryset(self):
        return self.evaluation_run

    @cached_property
    def evaluation_run(self) -> EvaluationRun:
        return get_object_or_404(
            EvaluationRun.objects.select_related("generation_experiment").filter(team__slug=self.kwargs["team_slug"]),
            pk=self.kwargs["evaluation_run_pk"],
        )

    def get_table_data(self):
        return self.evaluation_run.get_table_data()

    def get_table_class(self):
        """
        Inspect the first row’s keys and build a Table subclass
        with one Column per field.
        """
        data = self.get_table_data()
        if not data:
            return type("EmptyTable", (tables.Table,), {})

        attrs = {}
        for row in data:
            for key in row:
                if key in attrs:
                    continue
                attrs[key] = self.get_column(key)

        return type("EvaluationResultTableTable", (tables.Table,), attrs)

    def get_column(self, key):
        def session_url_factory(_, __, record, value):
            if not value or not self.evaluation_run.generation_experiment_id:
                return ""
            return reverse(
                "experiments:experiment_session_view",
                args=[self.kwargs["team_slug"], self.evaluation_run.generation_experiment.public_id, value],
            )

        header = key.replace("_", " ").title()
        match key:
            case "session":
                return actions.ActionsColumn(
                    verbose_name=header,
                    actions=[
                        actions.chip_action(label="Session", url_factory=session_url_factory),
                    ],
                    align="right",
                )
        return columns.Column(verbose_name=header)


@permission_required("evaluations.add_evaluationrun")
def create_evaluation_run(request, team_slug, evaluation_pk):
    config = get_object_or_404(EvaluationConfig, team__slug=team_slug, pk=evaluation_pk)
    run = config.run()
    return HttpResponseRedirect(reverse("evaluations:evaluation_results_home", args=[team_slug, evaluation_pk, run.pk]))


@permission_required("evaluations.add_evaluationrun")
def create_evaluation_preview(request, team_slug, evaluation_pk):
    config = get_object_or_404(EvaluationConfig, team__slug=team_slug, pk=evaluation_pk)
    run = config.run_preview()
    return HttpResponseRedirect(reverse("evaluations:evaluation_results_home", args=[team_slug, evaluation_pk, run.pk]))


@permission_required("evaluations.view_evaluationrun")
def download_evaluation_run_csv(request, team_slug, evaluation_pk, evaluation_run_pk):
    evaluation_run = get_object_or_404(
        EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team__slug=team_slug
    )
    filename = f"{evaluation_run.config.name}_results_{evaluation_run.id}.csv"
    table_data = list(evaluation_run.get_table_data(include_ids=True))
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    writer = csv.writer(response)

    if not table_data:
        writer.writerow(["No results available yet"])
        return response

    headers = list(table_data[0].keys())
    writer.writerow(headers)

    for row in table_data:
        writer.writerow([row.get(header, "") for header in headers])

    return response


@login_and_team_required
@require_http_methods(["GET"])
def load_experiment_versions(request, team_slug: str):
    experiment_id = request.GET.get("experiment")

    if not experiment_id:
        context = {
            "empty_message": "First select a chatbot above",
            "field_name": "experiment_version",
            "field_id": "id_experiment_version",
            "versions": [],
        }
        return render(request, "evaluations/partials/version_select.html", context)

    try:
        Experiment.objects.working_versions_queryset().exists(
            id=experiment_id,
            team=request.team,
        )
    except Experiment.DoesNotExist:
        context = {
            "empty_message": "Chatbot not found",
            "field_name": "experiment_version",
            "field_id": "id_experiment_version",
            "versions": [],
        }
        return render(request, "evaluations/partials/version_select.html", context)

    versions = Experiment.objects.all_versions_queryset(experiment_id).filter(team=request.team)
    choices = get_experiment_version_choices(versions)
    version_choices = [{"value": value, "label": label} for value, label in choices]

    context = {
        "empty_message": "Select a version...",
        "field_name": "experiment_version",
        "field_id": "id_experiment_version",
        "versions": version_choices,
        "help_text": "Specific chatbot version to use for evaluation.",
    }
    return render(request, "evaluations/partials/version_select.html", context)
