import inspect

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django_tables2 import SingleTableView
from pydantic import BaseModel

from apps.pipelines.flow import PipelineData
from apps.pipelines.models import Pipeline
from apps.pipelines.tables import PipelineTable
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class PipelineHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "pipelines",
            "title": "Pipelines",
            "new_object_url": reverse("pipelines:new", args=[team_slug]),
            "table_url": reverse("pipelines:table", args=[team_slug]),
        }


class PipelineTableView(SingleTableView):
    model = Pipeline
    paginate_by = 25
    table_class = PipelineTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Pipeline.objects.filter(team=self.request.team).annotate(run_count=Count("runs"))


class CreatePipeline(TemplateView):
    template_name = "pipelines/pipeline_builder.html"

    def get(self, request, *args, **kwargs):
        pipeline = Pipeline.objects.create(
            team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
        return redirect(reverse("pipelines:edit", args=args, kwargs={**kwargs, "pk": pipeline.id}))


class EditPipeline(TemplateView):
    template_name = "pipelines/pipeline_builder.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        return {**data, "pipeline_id": kwargs["pk"], "input_types": _pipeline_node_input_types()}


class DeletePipeline(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        pipeline = get_object_or_404(Pipeline, id=pk, team=request.team)
        pipeline.delete()
        messages.success(request, f"{pipeline.name} deleted")
        return HttpResponse()


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
        pipeline.data = data.data.model_dump()
        pipeline.save()
        return JsonResponse({"data": {"message": "Pipeline saved"}})

    try:
        pipeline = Pipeline.objects.get(pk=pk)
    except Pipeline.DoesNotExist:
        pipeline = Pipeline.objects.create(
            id=pk, team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
    return JsonResponse({"pipeline": {"id": pipeline.id, "name": pipeline.name, "data": pipeline.data}})
