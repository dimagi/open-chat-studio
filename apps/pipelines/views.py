import inspect

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.experiments.models import SourceMaterial
from apps.pipelines.flow import FlowPipelineData
from apps.pipelines.models import Pipeline, PipelineRun
from apps.pipelines.nodes.utils import get_input_types_for_node
from apps.pipelines.tables import PipelineRunTable, PipelineTable
from apps.service_providers.models import LlmProvider
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class PipelineHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "pipelines",
            "title": "Pipelines",
            "new_object_url": reverse("pipelines:new", args=[team_slug]),
            "table_url": reverse("pipelines:table", args=[team_slug]),
        }


class PipelineTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "pipelines.view_pipeline"
    model = Pipeline
    paginate_by = 25
    table_class = PipelineTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Pipeline.objects.filter(team=self.request.team, is_version=False, is_archived=False).annotate(
            run_count=Count("runs")
        )


class CreatePipeline(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.add_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get(self, request, *args, **kwargs):
        pipeline = Pipeline.objects.create(
            team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
        return redirect(reverse("pipelines:edit", args=args, kwargs={**kwargs, "pk": pipeline.id}))


class EditPipeline(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.change_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        llm_providers = LlmProvider.objects.filter(team=self.request.team).values("id", "name", "llm_models").all()
        return {
            **data,
            "pipeline_id": kwargs["pk"],
            "input_types": _pipeline_node_input_types(),
            "parameter_values": _pipeline_node_parameter_values(self.request.team, llm_providers),
            "default_values": _pipeline_node_default_values(llm_providers),
        }


class DeletePipeline(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "pipelines.delete_pipeline"

    def delete(self, request, team_slug: str, pk: int):
        pipeline = get_object_or_404(Pipeline.objects.prefetch_related("node_set"), id=pk, team=request.team)
        pipeline.archive()
        messages.success(request, f"{pipeline.name} deleted")
        return HttpResponse()


def _pipeline_node_parameter_values(team, llm_providers):
    """Returns the possible values for each input type"""
    source_materials = SourceMaterial.objects.filter(team=team).values("id", "topic").all()

    return {
        "LlmProviderId": [{"id": provider["id"], "name": provider["name"]} for provider in llm_providers],
        "LlmModel": {provider["id"]: provider["llm_models"] for provider in llm_providers},
        "SourceMaterialId": [{"id": material["id"], "topic": material["topic"]} for material in source_materials],
    }


def _pipeline_node_default_values(llm_providers):
    """Returns the default values for each input type"""
    try:
        provider_id = llm_providers[0]["id"]
    except (IndexError, KeyError):
        provider_id = None

    try:
        llm_model = llm_providers[0]["llm_models"][0]
    except (IndexError, KeyError):
        llm_model = None
    return {
        "LlmProviderId": provider_id,
        "LlmModel": llm_model,
        "LlmTemperature": 0.7,
        "MaxTokenLimit": 8192,
    }


def _pipeline_node_input_types():
    """Returns all the input types for the various nodes"""

    from apps.pipelines.nodes import nodes

    fields = []

    node_classes = [
        cls
        for _, cls in inspect.getmembers(nodes, inspect.isclass)
        if issubclass(cls, nodes.PipelineNode) and cls != nodes.PipelineNode
    ]
    for node_class in node_classes:
        fields.append(get_input_types_for_node(node_class))

    return fields


@login_and_team_required
@csrf_exempt
def pipeline_data(request, team_slug: str, pk: int):
    if request.method == "POST":
        pipeline = get_object_or_404(Pipeline.objects.prefetch_related("node_set"), pk=pk, team=request.team)
        data = FlowPipelineData.model_validate_json(request.body)
        pipeline.name = data.name
        pipeline.data = data.data.model_dump()
        pipeline.save()
        pipeline.set_nodes(data.data.nodes)
        return JsonResponse({"data": {"message": "Pipeline saved"}})

    try:
        pipeline = Pipeline.objects.get(pk=pk)
    except Pipeline.DoesNotExist:
        pipeline = Pipeline.objects.create(
            id=pk, team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
    return JsonResponse({"pipeline": {"id": pipeline.id, "name": pipeline.name, "data": pipeline.flow_data}})


@login_and_team_required
@permission_required("pipelines.view_pipeline")
def pipeline_details(request, team_slug: str, pk: int):
    pipeline = get_object_or_404(Pipeline, id=pk, team=request.team)
    return TemplateResponse(
        request,
        "pipelines/pipeline_details.html",
        {
            "pipeline": pipeline,
        },
    )


class PipelineRunsTableView(SingleTableView, PermissionRequiredMixin):
    permission_required = "pipelines.view_pipelinerun"
    model = PipelineRun
    paginate_by = 25
    table_class = PipelineRunTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return PipelineRun.objects.filter(pipeline=self.kwargs["pk"]).order_by("-created_at")


@login_and_team_required
@permission_required("pipelines.view_pipelinerun")
def run_details(request, team_slug: str, run_pk: int, pipeline_pk: int):
    pipeline_run = get_object_or_404(PipelineRun, id=run_pk, pipeline__id=pipeline_pk)
    if pipeline_run.pipeline.team.slug != team_slug:
        raise Http404()
    return render(
        request,
        "pipelines/pipeline_run_details.html",
        {"pipeline_run": pipeline_run},
    )
