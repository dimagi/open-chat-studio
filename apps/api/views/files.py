from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from apps.files.models import File


class BinaryRenderer(BaseRenderer):
    media_type = "application/octet-stream"
    format = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        # For binary content (file downloads), return data as-is
        # This renderer is only used for successful file responses
        return data


@extend_schema(operation_id="file_content", summary="Download File Content", tags=["Files"], responses=bytes)
class FileContentView(APIView):
    required_scopes = ("sessions:read", "chatbots:read")
    renderer_classes = [BinaryRenderer]

    def get(self, request, pk: int):
        file = get_object_or_404(File, id=pk, team=request.team)
        if not file.file:
            raise Http404()

        try:
            return FileResponse(file.file.open(), as_attachment=True, filename=file.file.name)
        except FileNotFoundError:
            raise Http404() from None
