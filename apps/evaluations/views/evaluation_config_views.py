import csv
import json
import logging
from functools import cached_property
from io import StringIO

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView, columns, tables

from apps.evaluations.const import EVALUATION_RUN_FIXED_HEADERS
from apps.evaluations.forms import EvaluationConfigForm, get_experiment_version_choices
from apps.evaluations.models import EvaluationConfig, EvaluationRun, EvaluationRunStatus, EvaluationRunType, Evaluator
from apps.evaluations.tables import EvaluationConfigTable, EvaluationRunTable
from apps.evaluations.tasks import upload_evaluation_run_results_task
from apps.evaluations.utils import get_evaluators_with_schema
from apps.experiments.models import Experiment
from apps.generics import actions
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

logger = logging.getLogger(__name__)


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

    all_headers = set()
    for row in table_data:
        all_headers.update(row.keys())

    other_headers = sorted([h for h in all_headers if h not in EVALUATION_RUN_FIXED_HEADERS and h != "error"])
    headers = [h for h in EVALUATION_RUN_FIXED_HEADERS if h in all_headers] + other_headers + ["error"]
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


@login_and_team_required
@permission_required("evaluations.change_evaluationrun")
def update_evaluation_run_results(request, team_slug: str, evaluation_pk: int, evaluation_run_pk: int):
    """Upload CSV to update evaluation run results"""
    evaluation_run = get_object_or_404(
        EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team__slug=team_slug
    )
    if request.method == "GET":
        context = {
            "active_tab": "evaluations",
            "title": "Upload Results",
            "evaluation_run": evaluation_run,
            "all_team_evaluators": Evaluator.objects.filter(team=request.team),
        }
        return render(request, "evaluations/evaluation_run_update.html", context)
    elif request.method == "POST":
        try:
            payload = json.loads(request.body)
            csv_data = payload.get("csv_data", [])
            column_mappings = payload.get("column_mappings", {})

            task = upload_evaluation_run_results_task.delay(
                evaluation_run.id, csv_data, request.team.id, column_mappings
            )
            return JsonResponse({"success": True, "task_id": task.id})
        except Exception as e:
            logger.error(f"Error starting CSV upload for evaluation run {evaluation_run.id}: {str(e)}")
            return JsonResponse({"error": "An error occurred while starting the CSV upload"}, status=500)


@login_and_team_required
@require_POST
def parse_evaluation_results_csv_columns(request, team_slug: str):
    """Parse uploaded CSV and return column names and sample data for evaluation results."""
    try:
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            return JsonResponse({"error": "No CSV file provided"}, status=400)

        file_content = csv_file.read().decode("utf-8")
        csv_reader = csv.DictReader(StringIO(file_content))
        columns = csv_reader.fieldnames or []

        all_rows = list(csv_reader)
        sample_rows = all_rows[:3]
        total_rows = len(all_rows)

        protected_columns = set(EVALUATION_RUN_FIXED_HEADERS) | set(["error"])

        result_columns = [col for col in columns if col not in protected_columns]
        suggestions = generate_evaluation_results_column_suggestions(result_columns, request.team)
        return JsonResponse(
            {
                "columns": columns,
                "result_columns": result_columns,
                "sample_rows": sample_rows,
                "all_rows": all_rows,
                "total_rows": total_rows,
                "suggestions": suggestions,
            }
        )

    except Exception:
        logger.warning("Error parsing evaluation results CSV")
        return JsonResponse({"error": "An error occurred while parsing the CSV file."}, status=400)


def generate_evaluation_results_column_suggestions(result_columns, team):
    """Generate suggestions for mapping result columns to evaluators."""
    evaluators = Evaluator.objects.filter(team=team)
    evaluator_name_to_id = {evaluator.name: evaluator.id for evaluator in evaluators}

    suggestions = {}

    for column in result_columns:
        suggested_evaluator_id = None
        if " (" in column and column.endswith(")"):
            evaluator_name_in_column = column[column.rfind("(") + 1 : -1]
            if evaluator_name_in_column in evaluator_name_to_id:
                suggested_evaluator_id = evaluator_name_to_id[evaluator_name_in_column]
        suggestions[column] = suggested_evaluator_id

    return suggestions
