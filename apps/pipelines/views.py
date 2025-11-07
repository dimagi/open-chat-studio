import inspect
import json

from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.db.models import QuerySet, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django_htmx.http import reswap
from django_tables2 import SingleTableView

from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.form_utils import get_custom_action_operation_choices
from apps.documents.models import Collection
from apps.experiments.models import AgentTools, BuiltInTools, Experiment, SourceMaterial
from apps.pipelines.flow import FlowPipelineData
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import OptionsSource
from apps.pipelines.tables import PipelineTable
from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Flag
from apps.web.waf import WafRule, waf_allow

from ..experiments.helpers import update_experiment_name_by_pipeline_id
from ..generics.chips import Chip
from ..generics.help import render_help_with_link
from ..utils.prompt import PromptVars


class PipelineHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    """View for listing event pipelines."""

    permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "pipelines",
            "title": "Event Pipelines",
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


class PipelineTableView(SingleTableView, PermissionRequiredMixin):
    """Displays a table of event pipelines for the current team."""

    permission_required = "pipelines.view_pipeline"
    model = Pipeline
    table_class = PipelineTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Pipeline.objects.filter(
            team=self.request.team, working_version=None, is_archived=False, experiment=None
        ).order_by("name")


class CreatePipeline(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.add_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get(self, request, *args, **kwargs):
        pipeline = Pipeline.create_default(request.team)
        return redirect(reverse("pipelines:edit", args=args, kwargs={**kwargs, "pk": pipeline.id}))


class EditPipeline(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.change_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        llm_providers = LlmProvider.objects.filter(team=self.request.team).values("id", "name", "type").all()
        llm_provider_models = LlmProviderModel.objects.for_team(self.request.team).all()
        pipeline = Pipeline.objects.get(id=kwargs["pk"], team=self.request.team)
        return {
            **data,
            "pipeline_id": kwargs["pk"],
            "pipeline_name": pipeline.name,
            "node_schemas": _pipeline_node_schemas(),
            "parameter_values": _pipeline_node_parameter_values(
                team=self.request.team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=[],
                include_versions=pipeline.is_a_version,
            ),
            "default_values": _pipeline_node_default_values(llm_providers, llm_provider_models),
            "flags_enabled": [flag.name for flag in Flag.objects.all() if flag.is_active_for_team(self.request.team)],
            "read_only": pipeline.is_a_version,
        }


class DeletePipeline(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
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

            response = render_to_string(
                "generic/referenced_objects.html",
                context={
                    "object_name": "pipeline",
                    "experiments": experiments,
                    "static_trigger_experiments": static_trigger_experiments,
                },
            )

        return reswap(HttpResponse(response, status=400), "none")


def _pipeline_node_parameter_values(team, llm_providers, llm_provider_models, synthetic_voices, include_versions=False):
    """Returns the possible values for each input type"""
    common_filters = {"team": team}
    if not include_versions:
        common_filters["working_version"] = None
    source_materials = SourceMaterial.objects.filter(**common_filters).values("id", "topic").all()
    assistants = OpenAiAssistant.objects.filter(**common_filters).values("id", "name").all()
    collections = Collection.objects.filter(**common_filters).filter(is_index=False).values("id", "name").all()
    # Until OCS fully supports RAG, we can only use remote indexes
    collection_indexes = (
        Collection.objects.filter(**common_filters).filter(team=team, is_index=True).values("id", "name").all()
    )

    def _option(value, label, type_=None, edit_url: str | None = None, max_token_limit=None):
        data = {"value": value, "label": label}
        data = data | ({"type": type_} if type_ else {})
        data = data | ({"edit_url": edit_url} if edit_url else {})
        data = data | ({"max_token_limit": max_token_limit} if max_token_limit else {})
        return data

    def _get_assistant_url(assistant_id: int):
        """
        Always link to the working version. If `working_version_id` is None, it means the assistant is the working
        version
        """
        return reverse("assistants:edit", args=[team.slug, assistant_id])

    custom_action_operations = []
    for _custom_action_name, operations_disp in get_custom_action_operation_choices(team):
        custom_action_operations.extend(operations_disp)

    mcp_tools = [
        (f"{server.id}:{tool}", f"{server.name}: {tool}")
        for server in team.mcpserver_set.all()
        for tool in server.available_tools
    ]

    return {
        "LlmProviderId": [_option(provider["id"], provider["name"], provider["type"]) for provider in llm_providers],
        "LlmProviderModelId": [
            _option(provider.id, str(provider), provider.type, None, provider.max_token_limit)
            for provider in llm_provider_models
        ],
        OptionsSource.source_material: (
            [_option("", "Select a topic")]
            + [_option(material["id"], material["topic"]) for material in source_materials]
        ),
        OptionsSource.assistant: (
            [_option("", "Select an Assistant")]
            + [
                _option(
                    value=assistant["id"],
                    label=assistant["name"],
                    edit_url=_get_assistant_url(assistant["id"]),
                )
                for assistant in assistants
            ]
        ),
        OptionsSource.collection: (
            [_option("", "Select a Collection")]
            + [
                _option(
                    value=collection["id"],
                    label=collection["name"],
                    edit_url=reverse(
                        "documents:single_collection_home", kwargs={"team_slug": team.slug, "pk": collection["id"]}
                    ),
                )
                for collection in collections
            ]
        ),
        OptionsSource.collection_index: (
            [_option("", "Select a Collection Index")]
            + [
                _option(
                    value=index["id"],
                    label=index["name"],
                    edit_url=reverse(
                        "documents:single_collection_home", kwargs={"team_slug": team.slug, "pk": index["id"]}
                    ),
                )
                for index in collection_indexes
            ]
        ),
        OptionsSource.agent_tools: [_option(value, label) for value, label in AgentTools.user_tool_choices()],
        OptionsSource.mcp_tools: [_option(value, label) for value, label in mcp_tools],
        OptionsSource.custom_actions: [_option(val, display_val) for val, display_val in custom_action_operations],
        OptionsSource.built_in_tools: {
            provider["type"].lower(): [
                _option(value, label) for value, label in BuiltInTools.choices_for_provider(provider["type"].lower())
            ]
            for provider in llm_providers
            if provider.get("type")
        },
        OptionsSource.built_in_tools_config: BuiltInTools.get_tool_configs_by_provider(),
        OptionsSource.text_editor_autocomplete_vars_llm_node: PromptVars.get_all_prompt_vars(),
        OptionsSource.text_editor_autocomplete_vars_router_node: PromptVars.get_router_prompt_vars(),
        OptionsSource.synthetic_voice_id: sorted(
            [
                _option(voice.id, str(voice), voice.service.lower()) | {"provider_id": voice.voice_provider_id}
                for voice in synthetic_voices
            ],
            key=lambda v: v["label"],
        ),
    }


def _pipeline_node_default_values(llm_providers: list[dict], llm_provider_models: QuerySet):
    llm_provider_model = None
    provider_id = None
    if len(llm_providers) > 0:
        for provider in llm_providers:
            llm_provider_model = llm_provider_models.filter(type=provider["type"]).first()
            if llm_provider_model:
                provider_id = provider["id"]
                break

    return {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": llm_provider_model.id if llm_provider_model else None,
    }


def _pipeline_node_schemas():
    from apps.pipelines.nodes import nodes

    schemas = []

    node_classes = [
        cls
        for _, cls in inspect.getmembers(nodes, inspect.isclass)
        if issubclass(cls, nodes.PipelineNode | nodes.PipelineRouterNode)
        and cls not in (nodes.PipelineNode, nodes.PipelineRouterNode)
    ]
    for node_class in node_classes:
        schemas.append(_get_node_schema(node_class))

    return schemas


def _get_node_schema(node_class):
    from apps.custom_actions.schema_utils import resolve_references

    schema = resolve_references(node_class.model_json_schema())
    schema.pop("$defs", None)

    # Remove type ambiguity for optional fields
    for _key, value in schema["properties"].items():
        if "anyOf" in value:
            any_of = value.pop("anyOf")
            value["type"] = [item["type"] for item in any_of if item["type"] != "null"][0]  # take the first type
    return schema


@waf_allow(WafRule.SizeRestrictions_BODY)
@login_and_team_required
@csrf_exempt
def pipeline_data(request, team_slug: str, pk: int):
    if request.method == "POST":
        with transaction.atomic():
            pipeline = get_object_or_404(Pipeline.objects.prefetch_related("node_set"), pk=pk, team=request.team)
            data = FlowPipelineData.model_validate_json(request.body)
            pipeline.name = data.name
            pipeline.data = data.data.model_dump()
            pipeline.save()
            pipeline.update_nodes_from_data()
            pipeline.refresh_from_db(fields=["node_set"])
            if getattr(data, "experiment_name", None):
                update_experiment_name_by_pipeline_id(pk, data.experiment_name)
        return JsonResponse({"data": pipeline.flow_data, "errors": pipeline.validate()})

    try:
        pipeline = Pipeline.objects.get(pk=pk)
    except Pipeline.DoesNotExist:
        pipeline = Pipeline.objects.create(
            id=pk, team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
    return JsonResponse(
        {
            "pipeline": {
                "id": pipeline.id,
                "name": pipeline.name,
                "data": pipeline.flow_data,
                "errors": pipeline.validate(),
            }
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
