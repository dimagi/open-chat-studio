import csv
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from functools import cached_property
from io import StringIO
from typing import Any

from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.conf import settings
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, TemplateView, UpdateView, View
from django_tables2 import SingleTableView, columns, tables

from apps.cost_tracking.services.reporting import (
    evaluation_config_cost_summary,
    evaluation_message_cost,
    evaluation_message_tokens,
    evaluation_run_cost,
    evaluation_run_costs,
)
from apps.evaluations.const import EVALUATION_RUN_FIXED_HEADERS
from apps.evaluations.exceptions import InFlightRunsError
from apps.evaluations.export import (
    CategoricalColumn,
    categorical_columns_for_evaluators,
    evaluator_output_columns,
    write_evaluation_csv,
)
from apps.evaluations.forms import EvaluationConfigForm, get_experiment_version_choices
from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    Evaluator,
    raise_if_runs_in_flight,
)
from apps.evaluations.tables import EvaluationConfigTable, EvaluationRunTable
from apps.evaluations.tagging import remove_applied_tags_for_runs
from apps.evaluations.tasks import (
    export_evaluation_bulk_results_task,
    upload_evaluation_run_results_task,
)
from apps.evaluations.utils import build_trend_data, filter_aggregates_for_display, get_evaluators_with_schema
from apps.experiments.models import Experiment
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.models import Trace
from apps.utils.time import seconds_to_human

logger = logging.getLogger(__name__)


class EvaluationHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "evaluations.view_evaluationconfig"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "evaluations",
            "title": "Evaluations",
            "page_title": "Evaluations",
            "new_object_url": reverse("evaluations:new", args=[team_slug]),
            "table_url": reverse("evaluations:table", args=[team_slug]),
            # "title_help_content": render_help_with_link(
            #     "Pipelines allow you to create more complex bots by combining one or more steps together.", "pipelines"  # noqa
            # ),
        }


class EvaluationTableView(PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    permission_required = "evaluations.view_evaluationconfig"
    model = EvaluationConfig
    table_class = EvaluationConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return EvaluationConfig.objects.filter(team=self.request.team).order_by("-created_at")


class CreateEvaluation(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "evaluations.add_evaluationconfig"
    template_name = "evaluations/evaluation_config_form.html"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    extra_context = {
        "title": "Create Evaluation",
        "page_title": "Create Evaluation",
        "button_text": "Create",
        "active_tab": "evaluations",
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


class EditEvaluation(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "evaluations.change_evaluationconfig"
    model = EvaluationConfig
    form_class = EvaluationConfigForm
    template_name = "evaluations/evaluation_config_form.html"
    extra_context = {
        "title": "Update Evaluation",
        "page_title": "Update Evaluation",
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


class DeleteEvaluation(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "evaluations.delete_evaluationconfig"

    def delete(self, request, team_slug: str, pk: int):
        """Delete the config, returning 409 if a related run is still in progress."""
        evaluation = get_object_or_404(EvaluationConfig, team=request.team, pk=pk)
        try:
            evaluation.delete()
        except InFlightRunsError as e:
            return HttpResponse(", ".join(e.messages), status=409)
        response = HttpResponse(status=200)
        if request.GET.get("redirect") == "1":
            response["HX-Redirect"] = reverse("evaluations:home", args=[self.request.team.slug])
        return response


class EvaluationRunHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/evaluation_runs_home.html"
    extra_context = {
        "active_tab": "evaluations",
        "title": "Evaluation Runs",
        "page_title": "Evaluation Runs",
        "allow_new": False,
    }

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        config = get_object_or_404(EvaluationConfig, id=kwargs["evaluation_pk"], team=self.request.team)

        return {
            **super().get_context_data(**kwargs),
            "config": config,
            "table_url": reverse("evaluations:evaluation_runs_table", args=[team_slug, kwargs["evaluation_pk"]]),
            "trends_url": reverse("evaluations:evaluation_trends", args=[team_slug, kwargs["evaluation_pk"]]),
            "cost_summary": evaluation_config_cost_summary(config),
        }


class ClearEvaluationRuns(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Un-apply the tags this config's runs created, then delete all of its runs."""

    permission_required = "evaluations.delete_evaluationrun"

    def post(self, request, team_slug: str, evaluation_pk: int):
        """Un-apply this config's eval tags and delete all its runs, returning 409 if any run is in progress."""
        config = get_object_or_404(EvaluationConfig, id=evaluation_pk, team=request.team)
        run_ids = list(EvaluationRun.objects.filter(config=config).values_list("id", flat=True))
        runs = EvaluationRun.objects.filter(id__in=run_ids)
        try:
            raise_if_runs_in_flight(runs, "evaluation")
        except InFlightRunsError as e:
            return HttpResponse(", ".join(e.messages), status=409)
        with transaction.atomic():
            remove_applied_tags_for_runs(runs)
            runs.delete()
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("evaluations:evaluation_runs_home", args=[team_slug, evaluation_pk])
        return response


class EvaluationTrendsView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/components/trend_charts.html"

    DATE_RANGE_CHOICES = [
        ("7", "Last 7 days"),
        ("30", "Last 30 days"),
        ("90", "Last 90 days"),
        ("all", "All time"),
    ]

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        config = get_object_or_404(EvaluationConfig, id=kwargs["evaluation_pk"], team=self.request.team)

        date_range = self.request.GET.get("range", "30")

        queryset = EvaluationRun.objects.filter(
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type__in=[EvaluationRunType.FULL, EvaluationRunType.DELTA],
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


class EvaluationRunTableView(PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    permission_required = "evaluations.view_evaluationrun"
    model = EvaluationRun
    table_class = EvaluationRunTable
    template_name = "evaluations/evaluation_runs_table.html"

    def get_queryset(self):
        return (
            EvaluationRun.objects.filter(
                config_id=self.kwargs["evaluation_pk"],
                type__in=[EvaluationRunType.FULL, EvaluationRunType.DELTA],
            )
            .prefetch_related("scoped_messages")
            .order_by("-created_at")
        )

    def get_table(self, **kwargs):
        """Stamp cost onto the rows of the *current page* only, after pagination has
        already sliced them. Leaving `get_table_data` at its default (the lazy
        `get_queryset()`) keeps pagination at the DB level (COUNT + LIMIT/OFFSET) rather
        than loading every run for the config on every request.
        """
        table = super().get_table(**kwargs)
        page_runs = [row.record for row in table.paginated_rows]
        if page_runs:
            costs = evaluation_run_costs(self.kwargs["evaluation_pk"], [run.id for run in page_runs])
            for run in page_runs:
                run.cost = costs.get(run.id)
        return table


class EvaluationResultHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/evaluation_result_home.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        evaluation_run = get_object_or_404(
            EvaluationRun, id=kwargs["evaluation_run_pk"], config_id=kwargs["evaluation_pk"], team=self.request.team
        )

        title = (
            "Evaluation Run Preview" if evaluation_run.type == EvaluationRunType.PREVIEW else "Evaluation Run Results"
        )
        run_cost = evaluation_run_cost(evaluation_run)
        context: dict[str, Any] = {
            "active_tab": "evaluations",
            "title": title,
            "page_title": title,
            "evaluation_run": evaluation_run,
            "allow_new": False,
            "run_cost": run_cost,
        }

        # Calculate duration if finished
        if evaluation_run.finished_at:
            duration = evaluation_run.finished_at - evaluation_run.created_at
            context["run_duration"] = seconds_to_human(duration.total_seconds())

        # Show progress if pending/processing, otherwise show results table
        total_results: int | None = None
        if evaluation_run.status in (EvaluationRunStatus.PENDING, EvaluationRunStatus.PROCESSING):
            context["celery_job_id"] = evaluation_run.job_id
            # Explicit None (not just absent) so the template's `default_if_none` can tell
            # "no count yet" from a terminal run that legitimately has zero results.
            context["total_results"] = None
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
            total_results = evaluation_run.results.count()
            context["total_results"] = total_results
            if evaluation_run.status == EvaluationRunStatus.COMPLETED:
                context.update(_aggregates_context(evaluation_run, team_slug))
                context["headline_category_stat"] = _headline_category_stat(context["aggregates"])

        if run_cost is not None and total_results:
            context["avg_cost_per_result"] = run_cost.total_cost / total_results

        return context


def _headline_category_stat(aggregates: list[dict]) -> dict[str, Any] | None:
    """The stat row's summary percentage: the mode value (and its share) of the first
    categorical field across the run's evaluators, in the same evaluator/field order the
    Aggregates card renders in - so the headline always matches whatever appears first
    there. No schema concept says which field or value is "the" one to headline, so
    "first" is the only generic, deterministic choice; returns None when the run has no
    categorical field to summarise.
    """
    for agg in aggregates:
        for stats in agg["aggregates"].values():
            if stats.get("type") != "categorical" or stats.get("mode") is None:
                continue
            pct = stats.get("distribution", {}).get(stats["mode"])
            if pct is not None:
                return {"value": stats["mode"], "pct": pct}
    return None


def _aggregates_context(evaluation_run: EvaluationRun, team_slug: str) -> dict[str, Any]:
    """Context for the aggregates partial, shared by the results page and its poll endpoint."""
    aggregates = filter_aggregates_for_display(evaluation_run.aggregates.select_related("evaluator").all())
    return {
        "aggregates": aggregates,
        # What the run has beats what the marker claims. A finalization that computed the aggregates
        # but died before stamping `finalized_at`, or an old-code worker mid-deploy that never stamps
        # it at all, would otherwise hide real results behind the spinner for the whole grace window.
        "finalizing": evaluation_run.is_finalizing and not aggregates,
        "aggregates_url": reverse(
            "evaluations:evaluation_run_aggregates",
            args=[team_slug, evaluation_run.config_id, evaluation_run.id],
        ),
    }


class EvaluationRunAggregatesView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Poll target for the aggregates block while a completed run is still being finalized."""

    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/components/aggregates.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        evaluation_run = get_object_or_404(
            EvaluationRun, id=kwargs["evaluation_run_pk"], config_id=kwargs["evaluation_pk"], team=self.request.team
        )
        return _aggregates_context(evaluation_run, team_slug)


@dataclass(frozen=True)
class ResultFilterPill:
    """One button in the results table's filter row: "All", or one specific
    (field, value) to narrow to. `field`/`value` are None for the "All" pill."""

    label: str
    field: str | None
    value: str | None
    active: bool


def _build_result_filter_pills(
    categorical_columns: list[CategoricalColumn], *, active_field: str | None, active_value: str | None
) -> list[ResultFilterPill]:
    """ "All" plus one pill per distinct value across every choice/binary output field in
    the run. Prefixed by field name only when more than one such field is in play, so the
    common case (one evaluator, one categorical field) gets bare value labels.
    """
    prefix_with_field = len(categorical_columns) > 1
    pills = [ResultFilterPill(label="All", field=None, value=None, active=active_field is None)]
    for column in categorical_columns:
        for value in column.values:
            label = f"{column.field_label}: {value.label}" if prefix_with_field else value.label
            pills.append(
                ResultFilterPill(
                    label=label,
                    field=column.column_key,
                    value=value.raw,
                    active=active_field == column.column_key and active_value == value.raw,
                )
            )
    return pills


class EvaluationResultDataMixin:
    """Row-building logic shared by the results table and its per-row detail panel: both
    need the same run/evaluators/columns/tokens/filtering so row order, field labels, and
    the currently-active filter pill stay consistent between the two views.
    """

    @cached_property
    def evaluation_run(self) -> EvaluationRun:
        return get_object_or_404(
            EvaluationRun.objects.select_related("generation_experiment").filter(team=self.request.team),
            pk=self.kwargs["evaluation_run_pk"],
        )

    @cached_property
    def evaluators(self) -> list[Evaluator]:
        return list(Evaluator.objects.filter(id__in=self.evaluation_run.evaluator_ids))

    @cached_property
    def categorical_columns(self) -> list[CategoricalColumn]:
        return categorical_columns_for_evaluators(self.evaluators)

    @cached_property
    def categorical_column_keys(self) -> set[str]:
        return {column.column_key for column in self.categorical_columns}

    @cached_property
    def dynamic_columns(self) -> list[tuple[str, str]]:
        """(column_key, label) for every evaluator output field. The results table
        shows a curated column per field (below), not the full grab-bag of context/tag/
        session-link columns `build_evaluation_table_data` also produces.
        """
        return evaluator_output_columns(self.evaluators)

    @cached_property
    def tokens_by_message(self) -> dict[int, int]:
        return evaluation_message_tokens(self.evaluation_run.config_id, self.evaluation_run.id)

    @cached_property
    def cost_by_message(self) -> dict[int, Decimal]:
        return evaluation_message_cost(self.evaluation_run.config_id, self.evaluation_run.id)

    def get_filter_field(self) -> str | None:
        field = self.request.GET.get("filter_field")
        return field if field in self.categorical_column_keys else None

    def get_filter_value(self) -> str | None:
        return self.request.GET.get("filter_value") if self.get_filter_field() else None

    @cached_property
    def _table_rows(self) -> list[dict]:
        """The actual (filtered, token-stamped) row list, memoized per request/instance.

        `get_table_data()` below must stay a plain method - django-tables2's own
        `SingleTableMixin.get_table()` calls `self.get_table_data()` as a method, and our
        `get_table_class()` calls it again to build columns - so without this cache, one
        request already re-runs the full DB query and per-message Python aggregation at
        least twice, and the detail view's prev/next re-triggers it again per click. That
        redundant full-run rebuild is what actually hurts once a run has 1000+ results,
        not the O(n) filter loop itself.
        """
        data = self.evaluation_run.get_table_data(include_ids=True)
        field = self.get_filter_field()
        if field is not None:
            value = self.get_filter_value()
            data = [row for row in data if str(row.get(field)) == value]
        for row in data:
            row["Tokens"] = self.tokens_by_message.get(row.get("id"))
            row["Cost"] = self.cost_by_message.get(row.get("id"))
        return data

    def get_table_data(self):
        """Return all table data for pagination, narrowed to the selected filter pill."""
        return self._table_rows


class EvaluationResultTableView(EvaluationResultDataMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    permission_required = "evaluations.view_evaluationrun"
    template_name = "evaluations/evaluation_results_table.html"
    table_pagination = {"per_page": 10}

    def get_queryset(self):
        return self.evaluation_run

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
        # Own URL (no query string) so the filter pills can re-fetch themselves with
        # `filter_field`/`filter_value` set, the same way `result_id` deep-links do.
        context["table_url"] = self.request.path
        context["filter_pills"] = _build_result_filter_pills(
            self.categorical_columns, active_field=self.get_filter_field(), active_value=self.get_filter_value()
        )
        # The "Results N total" heading lives in this fragment (not the parent template)
        # so it can sit on the same row as the filter pills, right-aligned, and both
        # re-render together on every pill click.
        context["total_results"] = self.evaluation_run.results.count()
        context["is_preview"] = self.evaluation_run.type == EvaluationRunType.PREVIEW
        return context

    def get_table_class(self):
        """Build a Table subclass with one Column per curated field: #, Dataset Input,
        Generated Response, one per evaluator output field, and Cost — not the full
        grab-bag of keys `build_evaluation_table_data` produces (message context, Applied
        Tags, session links aren't shown here).
        """
        data = self.get_table_data()
        if not data:
            return type("EmptyTable", (tables.Table,), {})

        highlight_result_id = self.get_highlight_result_id()
        filter_field = self.get_filter_field()
        filter_value = self.get_filter_value()

        column_keys = ["#", "Dataset Input", "Generated Response"]
        column_keys += [key for key, _label in self.dynamic_columns]
        column_keys.append("Cost")
        attrs = {key: self.get_column(key) for key in column_keys}

        # Define row class factory to add highlighting
        def _row_class_factory(record):
            class_defaults = settings.DJANGO_TABLES2_ROW_ATTRS["class"]
            classes = f"{class_defaults} cursor-pointer"
            if highlight_result_id and highlight_result_id == record.get("id"):
                classes = f"{classes} bg-yellow-100 dark:bg-yellow-900/20"
            return classes

        # Clicking anywhere on a row opens its detail panel (see EvaluationResultDetailView),
        # carrying the active filter pill along so the panel's prev/next navigation stays
        # within the same filtered subset the row was clicked from.
        def _row_hx_get_factory(record):
            url = reverse(
                "evaluations:evaluation_result_detail",
                args=[
                    self.kwargs["team_slug"],
                    self.kwargs["evaluation_pk"],
                    self.kwargs["evaluation_run_pk"],
                    record.get("id"),
                ],
            )
            if filter_field is not None:
                url = f"{url}?{urlencode({'filter_field': filter_field, 'filter_value': filter_value})}"
            return url

        # Create Meta class with row_attrs for highlighting, the detail-panel click target,
        # and data-result-id. Drops table-zebra from the default table attrs - alternating
        # row stripes read as noise against the badge/token columns here.
        Meta = type(
            "Meta",
            (),
            {
                "attrs": {
                    **settings.DJANGO_TABLES2_TABLE_ATTRS,
                    "class": "w-full table",
                    # Headers read as labels (DATASET INPUT, ACCEPTABILITY, ...) rather than
                    # sentence-case column names - `uppercase` is a pure display transform,
                    # so a dynamic evaluator-field label still renders correctly title-cased
                    # anywhere else it's used (e.g. the filter pills, the detail panel).
                    # whitespace-nowrap: a narrow, content-sized column (e.g. a single-digit
                    # "Score") otherwise wraps its own sort-arrow onto a second line once the
                    # uppercase label needs a touch more width than the column's data does.
                    "th": {
                        "class": (
                            f"{settings.DJANGO_TABLES2_TABLE_ATTRS['th']['class']} "
                            "uppercase tracking-wide whitespace-nowrap"
                        )
                    },
                },
                "row_attrs": {
                    **settings.DJANGO_TABLES2_ROW_ATTRS,
                    "class": _row_class_factory,
                    "data-result-id": lambda record: record.get("id", ""),
                    "hx-get": _row_hx_get_factory,
                    "hx-target": "#result-detail-panel",
                    "hx-swap": "innerHTML",
                },
            },
        )
        attrs["Meta"] = Meta

        return type("EvaluationResultTableTable", (tables.Table,), attrs)

    def get_column(self, key):
        if key in self.categorical_column_keys:
            column = next(c for c in self.categorical_columns if c.column_key == key)
            return columns.TemplateColumn(
                template_name="evaluations/components/category_badge_column.html",
                verbose_name=column.field_label,
                orderable=False,
                extra_context={"category_values": column.values},
            )

        dynamic_labels = dict(self.dynamic_columns)
        if key in dynamic_labels:
            # A free-text evaluator field (e.g. reasoning) can run just as long as the
            # dataset input/generated response below, so it gets the same clamp. Sortable,
            # same as the plain Column this replaces - only the rendering changes.
            return columns.TemplateColumn(
                template_name="evaluations/components/truncated_text_column.html",
                verbose_name=dynamic_labels[key],
            )

        match key:
            case "#":
                # Data is stored 0-based (see build_evaluation_table_data); shown 1-based so
                # a row's table number matches the "#N" heading in its detail panel.
                return columns.TemplateColumn(
                    template_code="{{ value|add:1 }}",
                    verbose_name="#",
                    orderable=False,
                )
            case "Dataset Input" | "Generated Response":
                # Clamped to 2 lines - these are free-text dataset/model output and can run
                # arbitrarily long, which would otherwise blow out every row's height. The
                # full text is still one click away in the detail panel.
                return columns.TemplateColumn(
                    template_name="evaluations/components/truncated_text_column.html",
                    verbose_name=key,
                )
            case "Cost":
                return columns.TemplateColumn(
                    template_code=(
                        "{% load cost_tracking %}"
                        "{% if value is not None %}${{ value|cost_display }}{% else %}—{% endif %}"
                    ),
                    verbose_name="Cost",
                    orderable=False,
                )
        return columns.Column(verbose_name=key)


class EvaluationResultDetailView(EvaluationResultDataMixin, PermissionRequiredMixin, View):
    """The side panel a results-table row opens into: one message's full input/output,
    every evaluator field (badges for categorical fields, plain text for the rest), cost,
    and links to its session/trace. Reuses `EvaluationResultDataMixin`'s row list so the
    panel's field labels, badge colors, and active filter pill match the table row the
    user clicked, and prev/next step through that same (possibly filtered) row order.
    """

    permission_required = "evaluations.view_evaluationrun"

    def get(self, request, *args, **kwargs):
        rows = self.get_table_data()
        message_id = kwargs["message_id"]
        index = next((i for i, row in enumerate(rows) if row.get("id") == message_id), None)
        if index is None:
            raise Http404("Result not found")
        row = rows[index]

        # A run's message has one EvaluationResult per evaluator; session/message data is
        # the same across all of them (see `_populate_message_row_fixed_fields`), so any one
        # row gives the session/trace links for the whole panel.
        result = (
            EvaluationResult.objects.select_related("session__experiment", "message__session__experiment")
            .filter(run=self.evaluation_run, message_id=message_id)
            .first()
        )
        if result is None:
            raise Http404("Result not found")

        team_slug = kwargs["team_slug"]
        evaluation_pk = kwargs["evaluation_pk"]
        evaluation_run_pk = kwargs["evaluation_run_pk"]
        filter_field = self.get_filter_field()
        filter_value = self.get_filter_value()

        def _detail_url(target_message_id):
            url = reverse(
                "evaluations:evaluation_result_detail",
                args=[team_slug, evaluation_pk, evaluation_run_pk, target_message_id],
            )
            if filter_field is not None:
                url = f"{url}?{urlencode({'filter_field': filter_field, 'filter_value': filter_value})}"
            return url

        badges = []
        for column in self.categorical_columns:
            value = row.get(column.column_key)
            if value in (None, ""):
                continue
            match = next((v for v in column.values if v.raw == str(value)), None)
            badges.append(
                {
                    "label": column.field_label,
                    "value": match.label if match else value,
                    "polarity": match.polarity if match else "neutral",
                }
            )

        text_fields = [
            {"label": label, "value": row[key]}
            for key, label in self.dynamic_columns
            if key not in self.categorical_column_keys and row.get(key) not in (None, "")
        ]

        # `result.session` is this run's own session (set for generation-mode runs, where a
        # new session is created to produce a response); `result.message.session` is the
        # source session the dataset message was imported from (message-mode evaluation
        # over existing chat data). Most runs are message-mode with no generation, so the
        # source session is what actually has a session to link to.
        session = result.session or result.message.session
        session_url = None
        if session and session.experiment_id:
            session_url = reverse(
                "chatbots:chatbot_session_view",
                args=[team_slug, session.experiment.public_id, session.external_id],
            )

        trace_url = None
        if result.message.input_chat_message_id:
            trace = (
                Trace.objects.filter(input_message_id=result.message.input_chat_message_id)
                .order_by("-timestamp")
                .first()
            )
            if trace:
                trace_url = trace.get_absolute_url()

        context = {
            "message_id": message_id,
            "result_number": row.get("#", index) + 1,
            "prev_url": _detail_url(rows[index - 1]["id"]) if index > 0 else None,
            "next_url": _detail_url(rows[index + 1]["id"]) if index < len(rows) - 1 else None,
            "dataset_input": row.get("Dataset Input"),
            "dataset_output": row.get("Dataset Output"),
            "generated_response": row.get("Generated Response"),
            "badges": badges,
            "text_fields": text_fields,
            "tokens": row.get("Tokens"),
            "cost": self.cost_by_message.get(message_id),
            "session_url": session_url,
            "trace_url": trace_url,
        }
        return render(request, "evaluations/components/evaluation_result_detail_panel.html", context)


@permission_required("evaluations.add_evaluationrun")
def create_evaluation_run(request, team_slug, evaluation_pk):
    config = get_object_or_404(EvaluationConfig, team=request.team, pk=evaluation_pk)
    run = config.run()
    return HttpResponseRedirect(reverse("evaluations:evaluation_results_home", args=[team_slug, evaluation_pk, run.pk]))


@permission_required("evaluations.add_evaluationrun")
def create_evaluation_preview(request, team_slug, evaluation_pk):
    config = get_object_or_404(EvaluationConfig, team=request.team, pk=evaluation_pk)
    run = config.run_preview()
    return HttpResponseRedirect(reverse("evaluations:evaluation_results_home", args=[team_slug, evaluation_pk, run.pk]))


@permission_required("evaluations.view_evaluationrun")
def download_evaluation_run_csv(request, team_slug, evaluation_pk, evaluation_run_pk):
    evaluation_run = get_object_or_404(EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team=request.team)
    filename = f"{evaluation_run.config.name}_results_{evaluation_run.id}.csv"
    table_data = list(evaluation_run.get_table_data(include_ids=True))
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    write_evaluation_csv(csv.writer(response), table_data)
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
    evaluation_run = get_object_or_404(EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team=request.team)
    if request.method == "GET":
        context = {
            "active_tab": "evaluations",
            "title": "Upload Results",
            "page_title": "Upload Results",
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
            EvaluationRun, id=evaluation_run_pk, config_id=evaluation_pk, team=request.team
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

        protected_columns = set(EVALUATION_RUN_FIXED_HEADERS) | {"error"}

        result_columns = [col for col in columns if col not in protected_columns and not col.startswith("error (")]
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


@login_and_team_required
@permission_required("evaluations.view_evaluationrun")
@require_POST
def start_bulk_download(request, team_slug: str, evaluation_pk: int):
    """Start an async bulk export of the most recent results per dataset item."""
    config = get_object_or_404(EvaluationConfig, id=evaluation_pk, team=request.team)
    task = export_evaluation_bulk_results_task.delay(config.id, request.team.id)
    return TemplateResponse(
        request,
        "evaluations/partials/bulk_download.html",
        {"config": config, "task_id": task.id},
    )


@login_and_team_required
@permission_required("evaluations.view_evaluationrun")
def get_bulk_download_link(request, team_slug: str, evaluation_pk: int, task_id: str):
    """Poll the bulk export task and return a download link when ready."""
    config = get_object_or_404(EvaluationConfig, id=evaluation_pk, team=request.team)
    info = Progress(AsyncResult(task_id)).get_info()
    context: dict = {"config": config}
    if info["complete"] and info["success"]:
        file_id = info["result"].get("file_id")
        if file_id:
            download_url = reverse("files:base", kwargs={"team_slug": team_slug, "pk": file_id}) + "?allow_s3=1"
            context["export_download_url"] = download_url
        else:
            context["export_error"] = info["result"].get("error", "Export failed.")
    elif info["complete"]:
        context["export_error"] = "Export failed."
    else:
        context["task_id"] = task_id
    return TemplateResponse(
        request,
        "evaluations/partials/bulk_download.html",
        context,
    )
