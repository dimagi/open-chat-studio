from django.contrib.auth.decorators import permission_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import BaseRenderer

from apps.files.models import File


class BinaryRenderer(BaseRenderer):
    media_type = "application/octet-stream"
    format = "bin"


@extend_schema(operation_id="file_content", summary="Download File Content", tags=["Files"], responses=bytes)
@api_view(["GET"])
@renderer_classes([BinaryRenderer])
@permission_required("files.view_file")
def file_content_view(request, pk: int):
    file = get_object_or_404(File, id=pk, team=request.team)
    if not file.file:
        raise Http404()

    try:
        return FileResponse(file.file.open(), as_attachment=True, filename=file.file.name)
    except FileNotFoundError:
        raise Http404() from None
