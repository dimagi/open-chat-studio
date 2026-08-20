import json

import pydantic
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.db.models import Subquery, prefetch_related_objects
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.events.models import EventActionType, StaticTrigger, StaticTriggerType, TimeoutTrigger
from apps.experiments.models import Experiment
from apps.pipelines.exceptions import MissingNodeDataError
from apps.pipelines.flow import FlowPipelineData, PipelineDiffPayload, split_flow_data
from apps.pipelines.jinja_utils import djlint_check, parse_jinja_template
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.node_metadata import (
    get_node_default_values,
    get_node_parameter_values,
    get_node_schemas,
)
from apps.pipelines.patching import apply_pipeline_patch
from apps.pipelines.tables import PipelineTable
from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.service_providers.llm_service.default_models import LLM_MODEL_PARAMETERS
from apps.service_providers.llm_service.model_parameters import LLM_MODEL_PARAMETER_SCHEMAS
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Flag
from apps.web.waf import WafRule, waf_allow

from ..generics.chips import Chip
from ..generics.help import render_help_with_link
from ..generics.referenced_objects import render_referenced_objects_modal


class PipelineHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """View for listing event pipelines."""

    permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "pipelines",
            "title": "Event Pipelines",
            "page_title": "Event Pipelines",
            "new_object_url": reverse("pipelines:new", args=[team_slug]),
            "table_url": reverse("pipelines:table", args=[team_slug]),
            "title_help_content": render_help_with_link(
                (
                    "Event pipelines allow you to combine steps together to create complex execution logic in response "
                    "to events."
                ),
                "pipelines",
            ),
        }


class PipelineTableView(PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    """Displays a table of event pipelines for the current team."""

    permission_required = "pipelines.view_pipeline"
    model = Pipeline
    table_class = PipelineTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Pipeline.objects.filter(
            team=self.request.team, working_version=None, is_archived=False, experiment=None
        ).order_by("name")


class CreatePipeline(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "pipelines.add_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get(self, request, *args, **kwargs):
        pipeline = Pipeline.create_default(request.team)
        return redirect(reverse("pipelines:edit", args=args, kwargs={**kwargs, "pk": pipeline.id}))


def _serialize_event_action(action):
    return {
        "action_type": action.action_type,
        "action_type_label": EventActionType(action.action_type).label,
        "params": action.params,
    }


def _get_chatbot_settings_context(experiment):
    return {
        "tracing_configured": bool(experiment.trace_provider_id),
        "convert_speech_inputs_to_text": bool(experiment.synthetic_voice_id and experiment.echo_transcript),
        "convert_output_text_to_speech": bool(experiment.synthetic_voice_id),
        "user_consent_required": bool(experiment.consent_form_id),
        "file_uploads_allowed": experiment.file_uploads_enabled,
    }


def get_widget_page_context(pipeline, experiment=None):
    if pipeline is None:
        return {}

    # data_without_positions rebuilds every node from its row, reading the collection_indexes M2M.
    prefetch_related_objects([pipeline], "node_set__collection_indexes")
    context = {
        "pipeline_structure": pipeline.data_without_positions,
    }

    if experiment is not None:
        static_triggers = StaticTrigger.objects.filter(experiment=experiment, is_archived=False).select_related(
            "action"
        )
        timeout_triggers = TimeoutTrigger.objects.filter(experiment=experiment, is_archived=False).select_related(
            "action"
        )

        context["chatbot"] = {
            "id": experiment.id,
            "name": experiment.name,
            "description": experiment.description,
            "version": experiment.version_number,
        }
        context["event_triggers"] = {
            "static_triggers": [
                {
                    "type": trigger.type,
                    "type_label": StaticTriggerType(trigger.type).label,
                    "action": _serialize_event_action(trigger.action),
                }
                for trigger in static_triggers
                if trigger.is_active
            ],
            "timeout_triggers": [
                {
                    "delay": trigger.delay,
                    "total_num_triggers": trigger.total_num_triggers,
                    "trigger_from_first_message": trigger.trigger_from_first_message,
                    "action": _serialize_event_action(trigger.action),
                }
                for trigger in timeout_triggers
                if trigger.is_active
            ],
        }
        context["settings"] = _get_chatbot_settings_context(experiment)

    return context


class EditPipeline(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "pipelines.change_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        pipeline = Pipeline.objects.get(id=kwargs["pk"], team=self.request.team)
        return {
            **data,
            "pipeline_id": kwargs["pk"],
            "pipeline_name": pipeline.name,
            "node_schemas": get_node_schemas(),
            "parameter_values": get_node_parameter_values(
                team=self.request.team,
                # A pipeline is edited outside any one chatbot, so there is no voice provider to offer.
                synthetic_voices=[],
                include_versions=pipeline.is_a_version,
            ),
            "default_values": get_node_default_values(self.request.team),
            "allow_edit_name": True,
            "flags_enabled": [flag.name for flag in Flag.objects.all() if flag.is_active_for_team(self.request.team)],
            "read_only": pipeline.is_a_version,
            "widget_page_context": get_widget_page_context(pipeline),
            **llm_model_parameter_context(),
        }


class DeletePipeline(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "pipelines.delete_pipeline"

    def delete(self, request, team_slug: str, pk: int):
        pipeline = get_object_or_404(Pipeline.objects.prefetch_related("node_set"), id=pk, team=request.team)
        if pipeline.archive():
            messages.success(request, "Pipeline Archived")
            return HttpResponse()
        else:
            experiments = [
                Chip(label=experiment.name, url=experiment.get_absolute_url())
                for experiment in pipeline.get_related_experiments_queryset()
            ]

            query = pipeline.get_static_trigger_experiment_ids()
            static_trigger_experiments = [
                Chip(label=experiment.name, url=experiment.get_absolute_url())
                for experiment in Experiment.objects.filter(id__in=Subquery(query)).all()
            ]

            return render_referenced_objects_modal(
                "pipeline",
                experiments=experiments,
                static_trigger_experiments=static_trigger_experiments,
            )


def llm_model_parameter_context():
    return {
        "llm_model_params": {model: param.__name__ for model, param in LLM_MODEL_PARAMETERS.items()},
        "llm_model_param_schemas": LLM_MODEL_PARAMETER_SCHEMAS,
    }


@waf_allow(WafRule.SizeRestrictions_BODY)
@login_and_team_required
@permission_required("pipelines.change_pipeline")
@csrf_exempt
def pipeline_data(request, team_slug: str, pk: int):
    if request.method == "POST":
        return _handle_pipeline_post(request, pk, team_slug)

    if request.method == "PATCH":
        return _handle_pipeline_patch(request, pk, team_slug)

    # flow_data below rebuilds every node from its row, reading the collection_indexes M2M.
    pipeline = get_object_or_404(
        Pipeline.objects.prefetch_related("node_set__collection_indexes"), pk=pk, team=request.team
    )

    return JsonResponse(
        {
            "pipeline": {
                "id": pipeline.id,
                "name": pipeline.name,
                "data": pipeline.flow_data,
                "edit_revision": pipeline.edit_revision,
                "errors": pipeline.validate(),
            }
        }
    )


def _handle_pipeline_post(request, pk: int, team_slug: str) -> JsonResponse:
    """Handle full-graph POST saves (backward-compatible)."""
    try:
        data = FlowPipelineData.model_validate_json(request.body)
    except pydantic.ValidationError as e:
        return JsonResponse({"error": f"Malformed payload: {e}"}, status=400)

    with transaction.atomic():
        pipeline = get_object_or_404(
            Pipeline.objects.prefetch_related("node_set__collection_indexes"), pk=pk, team=request.team
        )
        pipeline.name = data.name
        edge_data, node_data = split_flow_data(data.data)
        pipeline.data = edge_data.model_dump()
        pipeline.edit_revision += 1
        pipeline.save(update_fields=["name", "data", "edit_revision"])
        try:
            pipeline.update_nodes_from_data(node_data)
        except MissingNodeDataError as e:
            # A payload node without content for which no row exists — client error.
            # The message is built from the client-supplied node ids only.
            transaction.set_rollback(True)
            return JsonResponse({"error": f"No node data provided for new node(s): {e.node_ids}"}, status=400)
        pipeline.clear_node_caches()
    return JsonResponse(
        {
            "data": pipeline.flow_data,
            "errors": pipeline.validate(),
            "edit_revision": pipeline.edit_revision,
        }
    )


def _handle_pipeline_patch(request, pk: int, team_slug: str) -> JsonResponse:
    """Handle incremental PATCH saves with optimistic concurrency."""
    try:
        patch = PipelineDiffPayload.model_validate_json(request.body)
    except (pydantic.ValidationError, json.JSONDecodeError) as e:
        return JsonResponse({"error": f"Malformed payload: {e}"}, status=400)

    with transaction.atomic():
        pipeline = get_object_or_404(
            Pipeline.objects.select_for_update().prefetch_related("node_set__collection_indexes"),
            pk=pk,
            team=request.team,
        )

        if patch.base_revision != pipeline.edit_revision:
            return JsonResponse(
                {
                    "error": "Conflict: pipeline was modified by another session.",
                    "current_revision": pipeline.edit_revision,
                },
                status=409,
            )

        if patch.name is not None:
            pipeline.name = patch.name

        # The patch engine works off the full current graph: nodes rebuilt from the rows,
        # since Pipeline.data no longer lists them (ADR-0049).
        edge_data, node_data = apply_pipeline_patch(pipeline.flow_data, patch)
        pipeline.data = edge_data.model_dump()
        pipeline.edit_revision += 1
        pipeline.save(update_fields=["name", "data", "edit_revision"])
        try:
            pipeline.update_nodes_from_data(node_data)
        except MissingNodeDataError as e:
            # A diff node without content for which no row exists — client error.
            # The message is built from the client-supplied node ids only.
            transaction.set_rollback(True)
            return JsonResponse({"error": f"No node data provided for new node(s): {e.node_ids}"}, status=400)
        # flow_data was read above, off the pre-patch rows, so the response needs it rebuilt.
        pipeline.clear_node_caches()

    return JsonResponse(
        {
            "data": pipeline.flow_data,
            "errors": pipeline.validate(),
            "edit_revision": pipeline.edit_revision,
        }
    )


@login_and_team_required
@require_POST
@csrf_exempt
@permission_required("pipelines.change_pipeline")
def simple_pipeline_message(request, team_slug: str, pipeline_pk: int):
    message = json.loads(request.body).get("message")
    result = get_response_for_pipeline_test_message.delay(
        pipeline_id=pipeline_pk, message_text=message, user_id=request.user.id
    )
    return JsonResponse({"task_id": result.task_id})


@login_and_team_required
@csrf_exempt
@permission_required("pipelines.change_pipeline")
def get_pipeline_message_response(request, team_slug: str, pipeline_pk: int, task_id: str):
    progress = Progress(AsyncResult(task_id)).get_info()
    return JsonResponse(progress)


@login_and_team_required
@permission_required("pipelines.view_pipeline")
@require_POST
def validate_jinja(request, team_slug: str):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    template = body.get("template")
    if template is None:
        return JsonResponse({"error": "Missing 'template' field"}, status=400)
    if not isinstance(template, str):
        return JsonResponse({"error": "'template' must be a string"}, status=400)

    if len(template) > 50_000:
        return JsonResponse({"error": "Template too large (max 50,000 characters)"}, status=400)

    allowed_checks = {"jinja", "html"}
    checks_raw = body.get("checks", ["jinja", "html"])
    if not isinstance(checks_raw, list) or not all(isinstance(c, str) for c in checks_raw):
        return JsonResponse({"error": "'checks' must be a list of strings"}, status=400)
    checks = set(checks_raw)
    unknown_checks = checks - allowed_checks
    if unknown_checks:
        return JsonResponse({"error": f"Unsupported check(s): {', '.join(sorted(unknown_checks))}"}, status=400)
    errors = []

    # 1. Jinja syntax validation
    if "jinja" in checks and template:
        error = parse_jinja_template(template)
        if error:
            errors.append(
                {
                    "line": error.lineno or 1,
                    "column": 0,
                    "message": error.message,
                    "severity": "error",
                }
            )

    # 2. HTML linting via djlint (only if requested and no Jinja syntax errors)
    if "html" in checks and not errors and template:
        errors.extend(djlint_check(template))

    return JsonResponse({"errors": errors})
