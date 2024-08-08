from django.contrib.auth.decorators import permission_required
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
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
    examples=[
        OpenApiExample(
            name="CreateParticipantData",
            summary="Create participant data for multiple experiments",
            value={
                "identifier": "part1",
                "platform": "api",
                "data": [
                    {"experiment": "exp1", "data": {"name": "John"}},
                    {
                        "experiment": "exp2",
                        "data": {"name": "Doe"},
                        "schedules": [
                            {
                                "id": "sched1",
                                "name": "Schedule 1",
                                "date": "2022-01-01T00:00:00Z",
                                "prompt": "Prompt 1",
                            },
                        ],
                    },
                ],
            },
        ),
        OpenApiExample(
            name="UpdateParticipantSchedules",
            summary="Update and delete participant schedules",
            value={
                "identifier": "part1",
                "platform": "api",
                "data": [
                    {
                        "experiment": "exp1",
                        "schedules": [
                            {
                                "id": "sched1",
                                "name": "Schedule 1 updated",
                                "date": "2022-01-01T00:00:00Z",
                                "prompt": "Prompt updated",
                            },
                            {"id": "sched2", "delete": True},
                        ],
                    },
                ],
            },
        ),
    ],
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

    content_type = ContentType.objects.get_for_model(Experiment)
    for data in experiment_data:
        experiment = experiment_map[data["experiment"]]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type=content_type,
            object_id=experiment.id,
            team=team,
            defaults={"data": data["data"]} if data.get("data") else {},
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


@transaction.atomic()
def _create_update_schedules(team, experiment, participant, schedule_data):
    def _get_id(data):
        return data.get("id") or ScheduledMessage.generate_external_id(data["name"], experiment.id, participant.id)

    data_by_id = {_get_id(data): data for data in schedule_data if not data.get("delete")}
    existing_by_id = {
        message.external_id: message
        for message in ScheduledMessage.objects.filter(
            external_id__in=data_by_id.keys(), participant=participant, experiment=experiment
        )
    }
    new = []
    updated = []
    for external_id, data in data_by_id.items():
        if external_id in existing_by_id:
            message = existing_by_id.get(external_id)
            message.next_trigger_date = data["date"]
            message.custom_schedule_params["name"] = data["name"]
            message.custom_schedule_params["prompt_text"] = data["prompt"]
            updated.append(message)
        else:
            new.append(
                ScheduledMessage(
                    team=team,
                    experiment=experiment,
                    participant=participant,
                    next_trigger_date=data["date"],
                    external_id=external_id,
                    custom_schedule_params={
                        "name": data["name"],
                        "prompt_text": data["prompt"],
                        "repetitions": 1,
                        # these aren't really needed since it's one-off schedule
                        "frequency": 1,
                        "time_period": TimePeriod.DAYS,
                    },
                )
            )

    delete_ids = {data["id"] for data in schedule_data if data.get("delete")}
    if delete_ids:
        ScheduledMessage.objects.filter(external_id__in=delete_ids).delete()
    if updated:
        ScheduledMessage.objects.bulk_update(updated, fields=["next_trigger_date", "custom_schedule_params"])
    if new:
        ScheduledMessage.objects.bulk_create(new)


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
