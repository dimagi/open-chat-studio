from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from apps.pipelines.flow import PipelineData
from apps.pipelines.models import Pipeline
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def pipeline_builder(request, team_slug: str):
    return render(request, "pipelines/pipeline_builder.html")


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
            id=pk,
            team=request.team, data={"nodes": [], "edges": [], "viewport": {}}, name="New Pipeline"
        )
    return JsonResponse({"id": pipeline.id, "name": pipeline.name, "data": pipeline.data})
