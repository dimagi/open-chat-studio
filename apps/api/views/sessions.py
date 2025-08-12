import textwrap

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.serializers import ExperimentSessionCreateSerializer, ExperimentSessionSerializer
from apps.experiments.models import ExperimentSession

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


@extend_schema_view(
    list=extend_schema(
        operation_id="session_list",
        summary="List Experiment Sessions",
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
        ],
    ),
    retrieve=extend_schema(
        operation_id="session_retrieve",
        summary="Retrieve Experiment Session",
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
        summary="Create Experiment Session",
        tags=["Experiment Sessions"],
        request=ExperimentSessionCreateSerializer,
    ),
    end_experiment_session=extend_schema(
        operation_id="session_end",
        summary="End Experiment Session",
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
        summary="Update Experiment Session State",
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
)
class ExperimentSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView]
    serializer_class = ExperimentSessionSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    lookup_field = "external_id"
    lookup_url_kwarg = "id"

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
            .prefetch_related("chat__tags")
            .all()
        )
        if tags_query_param := self.request.query_params.get("tags"):
            queryset = queryset.filter(chat__tags__name__in=tags_query_param.split(","))
        if experiment_id := self.request.query_params.get("experiment"):
            queryset = queryset.filter(experiment__public_id=experiment_id)
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
