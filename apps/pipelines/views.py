from django.shortcuts import render

from apps.teams.decorators import login_and_team_required


@login_and_team_required
def pipeline_builder(request, team_slug: str):
    return render(request, "pipelines/pipeline_builder.html")
