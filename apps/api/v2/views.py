from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, ReadOnlyAPIKeyPermission
from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.api.v2.inspect.versioning import InspectVersionError, resolve_inspect_version
from apps.api.v2.lookups import get_working_chatbot, working_chatbots
from apps.api.v2.serializers import ChatbotSerializer, MeSerializer
from apps.api.v2.write.serializers import (
    ChatbotCreatedSerializer,
    ChatbotCreateSerializer,
    ChatbotDetailSerializer,
    ChatbotWriteSerializer,
)
from apps.oauth.permissions import TokenHasOAuthResourceScope


@extend_schema_view(
    list=extend_schema(
        operation_id="chatbot_list",
        summary="List Chatbots",
        description="List the chatbots belonging to the API key's team.",
        tags=["Chatbots"],
    ),
    retrieve=extend_schema(
        operation_id="chatbot_retrieve",
        summary="Retrieve Chatbot",
        description="Retrieve a single chatbot by its ID.",
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
    permission_classes = [*BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    serializer_class = ChatbotSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        return working_chatbots(self.request.team).select_related("team").prefetch_related("versions")

    @extend_schema(
        operation_id="chatbot_create",
        summary="Create Chatbot",
        description=(
            "Create a chatbot's working (draft) version, seeded with a Start -> LLM -> End "
            "pipeline. Nothing is published: use POST /chatbots/{id}/versions/ for that. On a team "
            "with no LLM provider the seed is Start + End with no edge between them, so the new "
            "chatbot reports pipeline_valid: false until you wire it. A key that is not listed "
            "below is rejected rather than ignored."
        ),
        tags=["Chatbots"],
        request=ChatbotCreateSerializer,
        responses={201: ChatbotCreatedSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = ChatbotCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.save()
        return Response(ChatbotCreatedSerializer(chatbot).data, status=status.HTTP_201_CREATED)

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

    @extend_schema(
        operation_id="chatbot_update",
        summary="Update Chatbot",
        description=(
            "Update the working (draft) chatbot's settings and its wiring to existing resources. "
            "The writable fields are the ones the chatbot settings page edits, with references "
            "given as ids (listed by GET /pipeline/options/). GET /chatbots/{id}/inspect/ returns "
            "more than this -- names, types and resolved values that describe a reference rather "
            "than address it -- so it is not a template for this body. Only the keys you send are "
            "changed, and a key that is not listed below is rejected rather than ignored."
        ),
        tags=["Chatbots"],
        parameters=[
            OpenApiParameter(
                name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
            ),
        ],
        request=ChatbotWriteSerializer,
        responses={200: ChatbotDetailSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        with transaction.atomic():
            # Model.save() writes every column, so without the row lock two concurrent PATCHes
            # naming different fields would silently clobber one another (spec W7).
            chatbot = get_working_chatbot(request.team, self.kwargs[self.lookup_url_kwarg], lock=True)
            serializer = ChatbotWriteSerializer(
                chatbot, data=request.data, partial=True, context=self.get_serializer_context()
            )
            serializer.is_valid(raise_exception=True)
            chatbot = serializer.save()
        return Response(ChatbotDetailSerializer(chatbot).data)


class MeView(APIView):
    """Return info about the authenticated user and their scoped team."""

    # Not BASE_PERMISSION_CLASSES: /me describes a human user, so a machine token (which has no user)
    # is refused by IsAuthenticated rather than admitted by IsAuthenticatedOrMachineToken.
    permission_classes = [IsAuthenticated, ReadOnlyAPIKeyPermission, TokenHasOAuthResourceScope]
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
