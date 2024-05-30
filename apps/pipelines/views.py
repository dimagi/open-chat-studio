import inspect

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from pydantic import BaseModel

from apps.pipelines.flow import PipelineData
from apps.pipelines.models import Pipeline
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def pipeline_builder(request, team_slug: str):
    context = {"input_types": _pipeline_node_input_types()}
    return render(request, "pipelines/pipeline_builder.html", context=context)


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
def get_pipeline(request, team_slug: str, pk: int):
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
