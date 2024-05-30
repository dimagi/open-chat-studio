import inspect

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from apps.pipelines.flow import PipelineData
from apps.pipelines.models import Pipeline
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def pipeline_builder(request, team_slug: str):
    return render(request, "pipelines/pipeline_builder.html")


def pipeline_node_input_types(request, team_slug):
    """Returns all the input types for the various nodes

    Example:
        {
          "CreateReport": {
            "prompt": "<class 'str'>"
          },
          "LLMResponse": {
            "llm_provider_id": "LlmProviderId",
            "llm_model": "LlmModel",
            "llm_temperature": "LlmTemperature"
          },
          "RenderTemplate": {
            "template_string": "PipelineJinjaTemplate"
          },
          "SendEmail": {
            "recipient_list": "list[str]",
            "subject": "<class 'str'>"
          }
        }
    """

    fields = {}
    from apps.pipelines.nodes import nodes

    node_classes = [cls for _, cls in inspect.getmembers(nodes, inspect.isclass) if issubclass(cls, nodes.PipelineNode)]
    for node_class in node_classes:
        fields[node_class.__name__] = {}
        for field_name, info in node_class.model_fields.items():
            fields[node_class.__name__][field_name] = str(info.annotation)
    return JsonResponse(fields)


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
    return JsonResponse({"id": pipeline.id, "name": pipeline.name, "data": pipeline.data})
