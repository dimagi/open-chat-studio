from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.v2.inspect.builder import InspectVersionError, build_inspect_payload, resolve_inspect_version
from apps.api.v2.serializers import ChatbotSerializer
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope


@extend_schema_view(
    list=extend_schema(
        operation_id="chatbot_list",
        summary="List Chatbots",
        tags=["Chatbots"],
    ),
    retrieve=extend_schema(
        operation_id="chatbot_retrieve",
        summary="Retrieve Chatbot",
        tags=["Chatbots"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="Chatbot ID",
            ),
        ],
    ),
)
class ChatbotViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    serializer_class = ChatbotSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        # Only the working (draft) version family heads are exposed at the top level.
        return Experiment.objects.filter(team=self.request.team).filter(working_version__isnull=True)

    @extend_schema(
        operation_id="chatbot_inspect",
        summary="Inspect Chatbot",
        tags=["Chatbots"],
        parameters=[
            OpenApiParameter(
                name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
            ),
            OpenApiParameter(
                name="version",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Which version to inspect: a version number, 'default' for the default published "
                    "version, or omit for the working (draft) version."
                ),
            ),
        ],
    )
    @action(detail=True, methods=["get"])
    def inspect(self, request, id=None):
        """Return a denormalized, read-only projection of the chatbot's full configuration."""
        family = self.get_object()
        try:
            target = resolve_inspect_version(family, request.query_params.get("version"))
        except InspectVersionError as err:
            raise NotFound(str(err)) from err
        return Response(build_inspect_payload(target))
