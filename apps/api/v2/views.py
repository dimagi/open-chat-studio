from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from apps.api.general.serializers import build_resource_serializer
from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.api.v2.inspect.versioning import InspectVersionError, resolve_inspect_version
from apps.api.v2.serializers import MeSerializer
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope


@extend_schema_view(
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
class ChatbotViewSet(mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_serializer_class(self):
        # Reuse the serializer the export `chatbots` resource is built from, so the single-chatbot
        # retrieve and the export list can't drift to different field sets.
        return build_resource_serializer(Experiment)

    def get_queryset(self):
        # `versions` is nested by the serializer; prefetch it so a chatbot's version family doesn't
        # cost a query per row.
        return Experiment.objects.filter(team=self.request.team, working_version__isnull=True).prefetch_related(
            "versions"
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
        """Return the chatbot's full configuration as a single read-only document."""
        try:
            target = resolve_inspect_version(
                public_id=self.kwargs[self.lookup_url_kwarg],
                version_param=request.query_params.get("version"),
                team=request.team,
            )
        except InspectVersionError as err:
            raise NotFound("Requested chatbot version was not found.") from err
        serializer = ChatbotInspectSerializer(target, context={"team": target.team})
        return Response(serializer.data)


class MeView(APIView):
    """Return info about the authenticated user and their scoped team."""

    permission_classes = [IsAuthenticated, TokenHasOAuthResourceScope]
    required_scopes = []  # Any valid OAuth token is accepted; no specific scope required.

    @extend_schema(
        operation_id="me",
        summary="Current User",
        description="Returns basic information about the authenticated user and the team the token is scoped to.",
        tags=["Me"],
        responses={200: MeSerializer},
    )
    def get(self, request):
        serializer = MeSerializer(request.user, context={"team": request.team})
        return Response(serializer.data)
