from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from apps.teams.decorators import login_and_team_required


@login_and_team_required
def pipeline_builder(request, team_slug: str):
    return render(request, "pipelines/pipeline_builder.html")


@login_and_team_required
@csrf_exempt
def get_pipeline(request, team_slug: str, pk: int):
    if request.method == "POST":
        # Save the pipeline
        print(request.body)
        return JsonResponse({"data": {"message": "Pipeline saved"}})
    return JsonResponse(
        {
            "data": {
                "nodes": [
                    {"id": "1", "position": {"x": 0, "y": 0}, "data": {"label": "1", "value": 123}, "type": "custom"},
                    {"id": "2", "position": {"x": 0, "y": 100}, "data": {"label": "2"}},
                ],
                "edges": [{"id": "e1-2", "source": "1", "target": "2"}],
            }
        }
    )
