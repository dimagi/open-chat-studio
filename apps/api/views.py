from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, mixins, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView, HasUserAPIKey
from apps.api.serializers import (
    ExperimentSerializer,
    ExperimentSessionCreateSerializer,
    ExperimentSessionSerializer,
    ParticipantExperimentData,
)
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData


@extend_schema_view(
    list=extend_schema(
        operation_id="experiment_list",
        summary="List Experiments",
    ),
    retrieve=extend_schema(
        operation_id="experiment_retrieve",
        summary="Retrieve Experiment",
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="Experiment ID",
            ),
        ],
    ),
)
class ExperimentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [HasUserAPIKey, DjangoModelPermissionsWithView]
    serializer_class = ExperimentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        return Experiment.objects.filter(team__slug=self.request.team.slug).all()


@extend_schema(
    operation_id="update_participant_data",
    summary="Update Participant Data",
    request=ParticipantExperimentData(many=True),
    responses={200: {}},
    parameters=[
        OpenApiParameter(
            name="participant_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Channel specific participant identifier",
        ),
    ],
)
@api_view(["POST"])
@permission_classes([HasUserAPIKey])
@permission_required("experiments.change_participantdata")
def update_participant_data(request, participant_id: str):
    """
    Upsert participant data for all specified experiments in the payload
    """
    serializer = ParticipantExperimentData(data=request.data, many=True).is_valid(raise_exception=True)
    serializer.is_valid(raise_exception=True)
    experiment_data = serializer.save()
    experiment_ids = {data["experiment"] for data in experiment_data}
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=request.team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}
    participant = get_object_or_404(Participant, identifier=participant_id, team=request.team)

    missing_ids = set(experiment_ids) - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        return JsonResponse(data=response, status=404)

    for data in experiment_data:
        experiment = experiment_map[data["experiment"]]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment.id,
            team=request.team,
            defaults={"team": experiment.team, "data": data["data"], "content_object": experiment},
        )
    return HttpResponse()


@extend_schema_view(
    list=extend_schema(
        operation_id="session_list",
        summary="List Experiment Sessions",
    ),
    retrieve=extend_schema(
        operation_id="session_retrieve",
        summary="Retrieve Experiment Session",
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="ID of the session",
            ),
        ],
    ),
    create=extend_schema(
        operation_id="session_create",
        summary="Create Experiment Session",
        request=ExperimentSessionCreateSerializer,
    ),
)
class ExperimentSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [HasUserAPIKey, DjangoModelPermissionsWithView]
    serializer_class = ExperimentSessionSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    lookup_field = "external_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        return ExperimentSession.objects.filter(team__slug=self.request.team.slug).all()

    def create(self, request, *args, **kwargs):
        serializer = ExperimentSessionCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = ExperimentSessionSerializer(instance=serializer.instance, context=self.get_serializer_context()).data
        headers = {"Location": str(output["url"])}
        return Response(output, status=status.HTTP_201_CREATED, headers=headers)
