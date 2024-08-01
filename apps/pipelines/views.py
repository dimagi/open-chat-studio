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
from pydantic import BaseModel

from apps.pipelines.flow import PipelineData
from apps.pipelines.models import Pipeline, PipelineRun
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
        return Pipeline.objects.filter(team=self.request.team).annotate(run_count=Count("runs"))


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
            "parameter_values": _pipeline_node_parameter_values(llm_providers),
            "default_values": _pipeline_node_default_values(llm_providers),
        }


class DeletePipeline(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "pipelines.delete_pipeline"

    def delete(self, request, team_slug: str, pk: int):
        pipeline = get_object_or_404(Pipeline, id=pk, team=request.team)
        pipeline.delete()
        messages.success(request, f"{pipeline.name} deleted")
        return HttpResponse()


def _pipeline_node_parameter_values(llm_providers):
    """Returns the possible values for each input type"""
    return {
        "LlmProviderId": [{"id": provider["id"], "name": provider["name"]} for provider in llm_providers],
        "LlmModel": {provider["id"]: provider["llm_models"] for provider in llm_providers},
    }


def _pipeline_node_default_values(llm_providers):
    """Returns the default values for each input type"""
    return {
        "LlmProviderId": llm_providers[0]["id"],
        "LlmModel": llm_providers[0]["llm_models"][0],
        "LlmTemperature": 0.7,
    }


def _pipeline_node_input_types():
    """Returns all the input types for the various nodes"""

    from apps.pipelines.nodes import nodes

    class InputParam(BaseModel):
        name: str
        type: str

    class NodeInputType(BaseModel):
        name: str
        human_name: str
        input_params: list[InputParam]

    fields = []

    node_classes = [
        cls
        for _, cls in inspect.getmembers(nodes, inspect.isclass)
        if issubclass(cls, nodes.PipelineNode) and cls != nodes.PipelineNode
    ]
    for node_class in node_classes:
        inputs = [
            InputParam(name=field_name, type=str(info.annotation))
            for field_name, info in node_class.model_fields.items()
        ]
        fields.append(
            NodeInputType(
                name=node_class.__name__,
                human_name=getattr(node_class, "__human_name__", node_class.__name__),
                input_params=inputs,
            ).model_dump()
        )
    return fields


@login_and_team_required
@csrf_exempt
def pipeline_data(request, team_slug: str, pk: int):
    if request.method == "POST":
        pipeline = get_object_or_404(Pipeline, pk=pk, team=request.team)
        data = PipelineData.model_validate_json(request.body)
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
    return JsonResponse({"pipeline": {"id": pipeline.id, "name": pipeline.name, "data": pipeline.data}})


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
