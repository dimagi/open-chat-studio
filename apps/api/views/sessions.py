import textwrap

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.annotations.models import Tag, TagCategories
from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.serializers import ExperimentSessionCreateSerializer, ExperimentSessionSerializer
from apps.experiments.models import ExperimentSession
from apps.oauth.permissions import TokenHasOAuthResourceScope

update_state_serializer = inline_serializer(
    name="update_state_serializer",
    fields={
        "state": serializers.JSONField(),
    },
)

update_state_response_serializer = inline_serializer(
    name="update_state_response",
    fields={
        "success": serializers.BooleanField(),
        "state": serializers.JSONField(),
    },
)

tags_request_serializer = inline_serializer(
    name="tags_request_serializer",
    fields={
        "tags": serializers.ListField(child=serializers.CharField()),
    },
)

tags_response_serializer = inline_serializer(
    name="tags_response_serializer",
    fields={
        "tags": serializers.ListField(child=serializers.CharField()),
    },
)


@extend_schema_view(
    list=extend_schema(
        operation_id="session_list",
        summary="List Chatbot Sessions",
        tags=["Experiment Sessions"],
        parameters=[
            OpenApiParameter(
                name="tags",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="A list of session tags (comma separated) to filter the results by",
            ),
            OpenApiParameter(
                name="experiment",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Experiment ID to filter sessions by",
            ),
            OpenApiParameter(
                name="versions",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Experiment versions (comma separated) to filter sessions by",
            ),
        ],
    ),
    retrieve=extend_schema(
        operation_id="session_retrieve",
        summary="Retrieve Chatbot Session",
        tags=["Experiment Sessions"],
        responses=ExperimentSessionSerializer(include_messages=True),
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID of the session",
            ),
        ],
        description=textwrap.dedent(
            """
            Retrieve the details of an session. This includes the messages exchanged during the session ordered
            by the creation date.
            """
        ),
    ),
    create=extend_schema(
        operation_id="session_create",
        summary="Create Chatbot Session",
        tags=["Experiment Sessions"],
        request=ExperimentSessionCreateSerializer,
    ),
    end_experiment_session=extend_schema(
        operation_id="session_end",
        summary="End Chatbot Session",
        tags=["Experiment Sessions"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID of the session",
            ),
        ],
        request=inline_serializer("end_session_serializer", {}),
        responses=inline_serializer("end_session_serializer", {}),
    ),
    update_state=extend_schema(
        operation_id="session_update_state",
        summary="Update Chatbot Session State",
        tags=["Experiment Sessions"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID of the session",
            ),
        ],
        request=update_state_serializer,
        responses={200: update_state_response_serializer},
    ),
    tags=extend_schema(
        operation_id="session_tags",
        summary="Manage Session Tags",
        tags=["Experiment Sessions"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID of the session",
            ),
        ],
        request=tags_request_serializer,
        responses={200: tags_response_serializer},
        description=textwrap.dedent(
            """
            Add or remove tags from a session.
            - POST: Add tags to the session (creates tags if they don't exist)
            - DELETE: Remove tags from the session
            """
        ),
    ),
)
class ExperimentSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    serializer_class = ExperimentSessionSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    lookup_field = "external_id"
    lookup_url_kwarg = "id"
    required_scopes = ["sessions"]

    def get_serializer(self, *args, **kwargs):
        action = self.action
        if action == "retrieve":
            kwargs["include_messages"] = True

        serializer_class = self.get_serializer_class()
        kwargs.setdefault("context", self.get_serializer_context())
        return serializer_class(*args, **kwargs)

    def get_queryset(self):
        queryset = (
            ExperimentSession.objects.filter(team__slug=self.request.team.slug)
            .select_related("team", "experiment", "participant")
            .prefetch_related("chat__tags", "chat__messages__tags")
            .all()
        )
        if tags_query_param := self.request.query_params.get("tags"):
            queryset = queryset.filter(chat__tags__name__in=tags_query_param.split(","))
        if experiment_id := self.request.query_params.get("experiment"):
            queryset = queryset.filter(experiment__public_id=experiment_id)
        if versions_param := self.request.query_params.get("versions"):
            version_list = versions_param.split(",")
            queryset = queryset.filter(
                chat__messages__tags__name__in=version_list,
                chat__messages__tags__category=TagCategories.EXPERIMENT_VERSION,
            ).distinct()
        return queryset

    def create(self, request, *args, **kwargs):
        # Custom create method because we use a different serializer processing the request than for
        # generating the response
        serializer = ExperimentSessionCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = ExperimentSessionSerializer(instance=serializer.instance, context=self.get_serializer_context()).data
        headers = {"Location": str(output["url"])}
        return Response(output, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"])
    def end_experiment_session(self, request, id):
        try:
            session = ExperimentSession.objects.get(external_id=id)
        except ExperimentSession.DoesNotExist:
            return Response({"error": "Session not found:{id}"}, status=status.HTTP_404_NOT_FOUND)
        session.end()
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"])
    def update_state(self, request, id):
        state = request.data.get("state")
        if not state:
            return Response({"error": "Missing 'state' in request"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ExperimentSession.objects.get(external_id=id)
        except ExperimentSession.DoesNotExist:
            return Response({"error": f"Session not found: {id}"}, status=status.HTTP_404_NOT_FOUND)

        session.state = state
        session.save()

        return Response({"success": True, "state": session.state}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post", "delete"])
    def tags(self, request, id):
        tag_names = request.data.get("tags")
        if not tag_names:
            return Response({"error": "Missing 'tags' in request"}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(tag_names, list):
            return Response({"error": "'tags' must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ExperimentSession.objects.get(external_id=id, team=request.team)
        except ExperimentSession.DoesNotExist:
            return Response({"error": f"Session not found: {id}"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "POST":
            # Add tags - create if they don't exist
            for tag_name in tag_names:
                tag, _ = Tag.objects.get_or_create(
                    name=tag_name,
                    team=request.team,
                    defaults={"created_by": request.user},
                )
                session.chat.add_tag(tag, request.team, added_by=request.user)
        elif request.method == "DELETE":
            # Remove tags
            tags_to_remove = Tag.objects.filter(name__in=tag_names, team=request.team)
            for tag in tags_to_remove:
                session.chat.tags.remove(tag)

        # Return updated tag list
        updated_tags = list(session.chat.tags.values_list("name", flat=True))
        return Response({"tags": updated_tags}, status=status.HTTP_200_OK)
