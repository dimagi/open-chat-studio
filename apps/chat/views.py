from django.contrib.auth.decorators import permission_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from apps.files.models import File
from apps.teams.decorators import login_and_team_required


@login_and_team_required
@permission_required("assistants.view_threadtoolresource")
def download_file(request, team_slug: str, pk: int):
    resource = get_object_or_404(File, team__slug=team_slug, id=pk, team=request.team)
    try:
        file = resource.file.open()
        return FileResponse(file, as_attachment=True, filename=resource.file.name)
    except FileNotFoundError:
        raise Http404()
