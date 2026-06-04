from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.v2.inspect.resources import ResourceFetcher
from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.api.v2.inspect.versioning import InspectVersionError, prefetch_inspect_target, resolve_inspect_version
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
        return (
            Experiment.objects.filter(team=self.request.team, working_version__isnull=True)
            .select_related("team")
            .prefetch_related("versions")
        )

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
        responses=ChatbotInspectSerializer,
    )
    @action(detail=True, methods=["get"])
    def inspect(self, request, id=None):
        """Return a denormalized, read-only projection of the chatbot's full configuration."""
        try:
            target = resolve_inspect_version(
                public_id=self.kwargs[self.lookup_url_kwarg],
                version_param=request.query_params.get("version"),
                team=request.team,
            )
        except InspectVersionError as err:
            raise NotFound("Requested chatbot version was not found.") from err
        target = prefetch_inspect_target(target)
        fetcher = ResourceFetcher.for_experiment(target)
        serializer = ChatbotInspectSerializer(target, context={"team": target.team, "fetcher": fetcher})
        return Response(serializer.data)
