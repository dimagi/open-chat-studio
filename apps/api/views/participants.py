from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from apps.api.serializers import ParticipantDataUpdateRequest
from apps.api.tasks import setup_connect_channels_for_bots
from apps.channels.models import ChannelPlatform
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import Experiment, Participant, ParticipantData


class UpdateParticipantDataView(APIView):
    required_scopes = ("participants:write",)
    permission_required = "experiments.change_participantdata"

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
    def post(self, request):
        return _update_participant_data(request)


@extend_schema(exclude=True)
class UpdateParticipantDataOldView(APIView):
    required_scopes = ("participants:write",)
    permission_required = "experiments.change_participantdata"

    def post(self, request):
        # This endpoint is kept for backwards compatibility of the path with a trailing "/"
        return _update_participant_data(request)


def _update_participant_data(request):
    """
    Upsert participant data for all specified experiments in the payload
    """
    serializer = ParticipantDataUpdateRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    identifier = serializer.data["identifier"]

    platform = serializer.data["platform"]
    team = request.team

    identifier = ChannelPlatform(platform).normalize_identifier(identifier)
    participant, _ = Participant.objects.get_or_create(identifier=identifier, team=team, platform=platform)

    # Update the participant's name if provided
    participant.update_name_from_data(serializer.data)

    experiment_data = serializer.data["data"]
    experiment_map = _get_participant_experiments(team, experiment_data)

    experiment_data_map = {}
    for data in experiment_data:
        experiment = experiment_map[data["experiment"]]

        participant_data, _created = ParticipantData.objects.update_or_create(
            participant=participant,
            experiment=experiment,
            team=team,
            defaults={"data": data["data"]} if data.get("data") else {},
        )

        if schedule_data := data.get("schedules"):
            _create_update_schedules(request, experiment, participant, schedule_data)

        if platform == ChannelPlatform.COMMCARE_CONNECT:
            experiment_data_map[experiment.id] = participant_data.id

    if platform == ChannelPlatform.COMMCARE_CONNECT:
        setup_connect_channels_for_bots.delay(connect_id=identifier, experiment_data_map=experiment_data_map)

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
def _create_update_schedules(request, experiment, participant, schedule_data):
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
            message = existing_by_id[external_id]
            message.next_trigger_date = data["date"]
            message.custom_schedule_params["name"] = data["name"]
            message.custom_schedule_params["prompt_text"] = data["prompt"]
            updated.append(message)
        else:
            new.append(
                ScheduledMessage(
                    team=request.team,
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
        ScheduledMessage.objects.filter(external_id__in=delete_ids).update(
            cancelled_at=timezone.now(), cancelled_by=request.user
        )
    if updated:
        ScheduledMessage.objects.bulk_update(updated, fields=["next_trigger_date", "custom_schedule_params"])
    if new:
        ScheduledMessage.objects.bulk_create(new)
