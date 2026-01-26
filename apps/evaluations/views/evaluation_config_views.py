import csv
import json
import logging
from datetime import timedelta
from functools import cached_property
from io import StringIO

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView, columns, tables

from apps.evaluations.const import EVALUATION_RUN_FIXED_HEADERS
from apps.evaluations.forms import EvaluationConfigForm, get_experiment_version_choices
from apps.evaluations.models import EvaluationConfig, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tables import EvaluationConfigTable, EvaluationRunTable
from apps.evaluations.tasks import upload_evaluation_run_results_task
from apps.evaluations.utils import build_trend_data, filter_aggregates_for_display, get_evaluators_with_schema
from apps.experiments.models import Experiment
from apps.generics import actions
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.time import seconds_to_human

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
    table_class = EvaluationConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return EvaluationConfig.objects.filter(team=self.request.team).order_by("-created_at")


class CreateEvaluation(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "evaluations.add_evaluationconfig"
    template_name = "evaluations/evaluation_config_form.html"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    extra_context = {"title": "Create Evaluation", "button_text": "Create", "active_tab": "evaluations"}

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
    template_name = "evaluations/evaluation_runs_home.html"
    extra_context = {
        "active_tab": "evaluations",
        "title": "Evaluation Runs",
        "allow_new": False,
    }

    def get_context_data(self, team_slug: str, **kwargs):
        config = get_object_or_404(EvaluationConfig, id=kwargs["evaluation_pk"], team__slug=team_slug)

        return {
            **super().get_context_data(**kwargs),
            "config": config,
            "table_url": reverse("evaluations:evaluation_runs_table", args=[team_slug, kwargs["evaluation_pk"]]),
            "trends_url": reverse("evaluations:evaluation_trends", args=[team_slug, kwargs["evaluation_pk"]]),
        }


class EvaluationTrendsView(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/components/trend_charts.html"

    DATE_RANGE_CHOICES = [
        ("7", "Last 7 days"),
        ("30", "Last 30 days"),
        ("90", "Last 90 days"),
        ("all", "All time"),
    ]

    def get_context_data(self, team_slug: str, **kwargs):
        config = get_object_or_404(EvaluationConfig, id=kwargs["evaluation_pk"], team__slug=team_slug)

        date_range = self.request.GET.get("range", "30")

        queryset = EvaluationRun.objects.filter(
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )

        if date_range != "all":
            try:
                days = int(date_range)
                cutoff_date = timezone.now() - timedelta(days=days)
                queryset = queryset.filter(created_at__gte=cutoff_date)
            except ValueError:
                pass  # Invalid range, show all

        completed_runs = list(queryset.prefetch_related("aggregates__evaluator").order_by("created_at"))
        trend_data = build_trend_data(completed_runs)

        return {
            "config": config,
            "trend_data": trend_data,
            "trend_data_json": trend_data,
            "date_range_choices": self.DATE_RANGE_CHOICES,
            "current_range": date_range,
            "trends_url": reverse("evaluations:evaluation_trends", args=[team_slug, kwargs["evaluation_pk"]]),
        }


class EvaluationRunTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    model = EvaluationRun
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

        # Calculate duration if finished
        if evaluation_run.finished_at:
            duration = evaluation_run.finished_at - evaluation_run.created_at
            context["run_duration"] = seconds_to_human(duration.total_seconds())

        # Show progress if running, otherwise show results table
        if evaluation_run.status in [EvaluationRunStatus.PROCESSING]:
            context["group_job_id"] = evaluation_run.job_id
        else:
            table_url = reverse(
                "evaluations:evaluation_results_table",
                args=[team_slug, kwargs["evaluation_pk"], kwargs["evaluation_run_pk"]],
            )
            result_id = self.request.GET.get("result_id")
            if result_id:
                table_url = f"{table_url}?result_id={result_id}"
            context["table_url"] = table_url
            # Add total results count
            context["total_results"] = evaluation_run.results.count()
            if evaluation_run.status == EvaluationRunStatus.COMPLETED:
                aggregates = evaluation_run.aggregates.select_related("evaluator").all()
                context["aggregates"] = filter_aggregates_for_display(aggregates)

        return context


class EvaluationResultTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/evaluation_results_table.html"
    table_pagination = {"per_page": 10}

    def get_queryset(self):
        return self.evaluation_run

    @cached_property
    def evaluation_run(self) -> EvaluationRun:
        return get_object_or_404(
            EvaluationRun.objects.select_related("generation_experiment").filter(team__slug=self.kwargs["team_slug"]),
            pk=self.kwargs["evaluation_run_pk"],
        )

    def get_table_data(self):
        """Return all table data for pagination."""
        return self.evaluation_run.get_table_data(include_ids=True)

    def get_table_pagination(self, table):
        """Configure pagination and calculate page for highlighted result."""
        highlight_result_id = self.get_highlight_result_id()
        page_size = self.table_pagination.get("per_page", 10)
        pagination_config = dict(self.table_pagination)

        # On first load with highlight, calculate which page contains the result
        if highlight_result_id and not self.request.GET.get("page"):
            all_data = self.get_table_data()
            result_index = None
            for idx, row in enumerate(all_data):
                if row.get("id") == highlight_result_id:
                    result_index = idx
                    break

            if result_index is not None:
                # Calculate which page contains this result and add to pagination config
                calculated_page = (result_index // page_size) + 1
                pagination_config["page"] = calculated_page

        return pagination_config

    def get_highlight_result_id(self):
        """Extract and validate the result_id query parameter for highlighting."""
        try:
            return int(self.request.GET.get("result_id"))
        except (ValueError, TypeError):
            return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["highlight_result_id"] = self.get_highlight_result_id()
        return context

    def get_table_class(self):
        """
        Inspect the first row's keys and build a Table subclass
        with one Column per field.
        """
        from django.conf import settings

        data = self.get_table_data()
        if not data:
            return type("EmptyTable", (tables.Table,), {})

        highlight_result_id = self.get_highlight_result_id()

        # Build column attributes
        attrs = {}
        for row in data:
            for key in row:
                if key in attrs:
                    continue
                attrs[key] = self.get_column(key)

        # Define row class factory to add highlighting
        def _row_class_factory(record):
            class_defaults = settings.DJANGO_TABLES2_ROW_ATTRS["class"]
            if highlight_result_id and highlight_result_id == record.get("id"):
                return f"{class_defaults} bg-yellow-100 dark:bg-yellow-900/20"
            return class_defaults

        # Create Meta class with row_attrs for highlighting and data-result-id
        Meta = type(
            "Meta",
            (),
            {
                "row_attrs": {
                    **settings.DJANGO_TABLES2_ROW_ATTRS,
                    "class": _row_class_factory,
                    "data-result-id": lambda record: record.get("id", ""),
                },
            },
        )
        attrs["Meta"] = Meta

        return type("EvaluationResultTableTable", (tables.Table,), attrs)

    def get_column(self, key):
        def session_url_factory(_, __, record, value):
            if not value or not self.evaluation_run.generation_experiment_id:
                return "#"  # Return placeholder URL to ensure button is rendered
            return reverse(
                "chatbots:chatbot_session_view",
                args=[self.kwargs["team_slug"], self.evaluation_run.generation_experiment.public_id, value],
            )

        def session_enabled_condition(_, record):
            # Check if session value exists (not empty string)
            return bool(record.get("session"))

        def dataset_url_factory(_, __, record, value):
            if not value:
                return "#"
            dataset_id = self.evaluation_run.config.dataset_id
            message_id = record.get("message_id")

            url = reverse("evaluations:dataset_edit", args=[self.kwargs["team_slug"], dataset_id])
            return f"{url}?message_id={message_id}"

        def dataset_enabled_condition(_, record):
            return bool(record.get("message_id"))

        header = key.replace("_", " ").title()
        match key:
            case "#":
                return columns.TemplateColumn(
                    template_name="evaluations/evaluation_result_id_column.html",
                    verbose_name=header,
                    orderable=False,
                    extra_context={
                        "team_slug": self.kwargs["team_slug"],
                        "evaluation_pk": self.kwargs["evaluation_pk"],
                        "evaluation_run_pk": self.kwargs["evaluation_run_pk"],
                    },
                )
            case "id":
                # Hide the id column but keep it in the data
                return columns.Column(verbose_name=header, visible=False)
            case "session":
                return actions.ActionsColumn(
                    verbose_name=header,
                    actions=[
                        actions.chip_action(
                            label="Session",
                            url_factory=session_url_factory,
                            enabled_condition=session_enabled_condition,
                        ),
                        actions.chip_action(
                            label=mark_safe('<i class="fa-solid fa-external-link"></i>'),
                            url_factory=dataset_url_factory,
                            enabled_condition=dataset_enabled_condition,
                            open_url_in_new_tab=True,
                        ),
                    ],
                    align="right",
                )
            case "message_id":
                # Skip rendering message_id as a separate column since it's now in session column
                return None
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

    if not Experiment.objects.filter(id=experiment_id, team=request.team, working_version=None).exists():
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
def parse_evaluation_results_csv_columns(request, team_slug: str, evaluation_pk: int, evaluation_run_pk: int):
    """Parse uploaded CSV and return column names and sample data for evaluation results."""
    try:
        evaluation_run = get_object_or_404(
            EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team__slug=team_slug
        )
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
        suggestions = generate_evaluation_results_column_suggestions(result_columns, evaluation_run)
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


def generate_evaluation_results_column_suggestions(result_columns, evaluation_run):
    """Generate suggestions for mapping result columns to evaluators."""
    evaluators = evaluation_run.config.evaluators.all()
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
