from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, mixins, status
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.serializers import (
    ExperimentSerializer,
    ExperimentSessionCreateSerializer,
    ExperimentSessionSerializer,
    ParticipantDataUpdateRequest,
)
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData


@extend_schema_view(
    list=extend_schema(
        operation_id="experiment_list",
        summary="List Experiments",
        tags=["Experiments"],
    ),
    retrieve=extend_schema(
        operation_id="experiment_retrieve",
        summary="Retrieve Experiment",
        tags=["Experiments"],
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
    permission_classes = [DjangoModelPermissionsWithView]
    serializer_class = ExperimentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        return Experiment.objects.filter(team__slug=self.request.team.slug).all()


@extend_schema(
    operation_id="update_participant_data",
    summary="Update Participant Data",
    tags=["Participants"],
    request=ParticipantDataUpdateRequest(),
    responses={200: {}},
)
@api_view(["POST"])
@permission_required("experiments.change_participantdata")
def update_participant_data(request):
    """
    Upsert participant data for all specified experiments in the payload
    """
    serializer = ParticipantDataUpdateRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    identifier = serializer.data["identifier"]
    platform = serializer.data["platform"]
    team = request.team
    participant, _ = Participant.objects.get_or_create(identifier=identifier, team=team, platform=platform)

    experiment_data = serializer.data["data"]
    experiment_map = _get_participant_experiments(team, experiment_data)

    for data in experiment_data:
        experiment = experiment_map[data["experiment"]]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment.id,
            team=team,
            defaults={"data": data["data"], "content_object": experiment},
        )

        if schedule_data := data.get("schedules"):
            _create_update_schedules(team, experiment, participant, schedule_data)
    return HttpResponse()


def _get_participant_experiments(team, experiment_data) -> dict[str, Experiment]:
    experiment_ids = {data["experiment"] for data in experiment_data}
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}

    missing_ids = experiment_ids - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        raise NotFound(detail=response)

    return experiment_map


def _create_update_schedules(team, experiment, participant, schedule_data):
    messages = [
        ScheduledMessage(
            team=team,
            experiment=experiment,
            participant=participant,
            next_trigger_date=data["date"],
            external_id=data.get("id"),
            custom_schedule_params={
                "name": data["name"],
                "prompt_text": data["prompt"],
                "repetitions": 1,
                # these aren't really needed since it's one-off schedule
                "frequency": 1,
                "time_period": TimePeriod.DAYS,
            },
        )
        for data in schedule_data
    ]
    for message in messages:
        message.assign_external_id()

    ScheduledMessage.objects.bulk_create(messages)


@extend_schema_view(
    list=extend_schema(
        operation_id="session_list",
        summary="List Experiment Sessions",
        tags=["Experiment Sessions"],
    ),
    retrieve=extend_schema(
        operation_id="session_retrieve",
        summary="Retrieve Experiment Session",
        tags=["Experiment Sessions"],
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
        tags=["Experiment Sessions"],
        request=ExperimentSessionCreateSerializer,
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

    def get_queryset(self):
        return ExperimentSession.objects.filter(team__slug=self.request.team.slug).all()

    def create(self, request, *args, **kwargs):
        serializer = ExperimentSessionCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = ExperimentSessionSerializer(instance=serializer.instance, context=self.get_serializer_context()).data
        headers = {"Location": str(output["url"])}
        return Response(output, status=status.HTTP_201_CREATED, headers=headers)
