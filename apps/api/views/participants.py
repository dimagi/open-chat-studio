import logging
import uuid

import httpx
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.pagination import CursorPagination
from apps.api.permissions import ReadOnlyAPIKeyPermission
from apps.api.serializers import ParticipantDataUpdateRequest, ParticipantDetailSerializer
from apps.api.tasks import create_connect_channel_for_participant
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import Experiment, Participant, ParticipantData
from apps.oauth.permissions import TokenHasOAuthResourceScope

logger = logging.getLogger("ocs.api")


class ServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Service temporarily unavailable."


class BadRequest(APIException):
    status_code = 400
    default_detail = "Bad request."


class ParticipantView(APIView):
    """GET: list participants for the team. POST: update/create participant data."""

    pagination_class = CursorPagination
    permission_classes = [IsAuthenticated, ReadOnlyAPIKeyPermission, TokenHasOAuthResourceScope]
    # base scope; TokenHasOAuthResourceScope appends :read for safe methods, :write otherwise.
    required_scopes = ("participants",)

    @extend_schema(
        operation_id="list_participants",
        summary="List Participants",
        tags=["Participants"],
        parameters=[
            OpenApiParameter(
                name="identifier",
                description="Filter by participant identifier",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="platform",
                description="Filter by platform (e.g. api, telegram, whatsapp)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="chatbot",
                description="Filter by chatbot public id; returns participants that have data for the chatbot.",
                required=False,
                type=OpenApiTypes.UUID,
            ),
        ],
        responses={200: ParticipantDetailSerializer(many=True)},
        examples=[
            OpenApiExample(
                name="ListParticipants",
                summary="A participant with their chatbot data",
                response_only=True,
                value={
                    "id": "e172ff63-2469-419f-a828-783fc9291bc7",
                    "identifier": "part1",
                    "name": "John",
                    "platform": "api",
                    "remote_id": "",
                    "data": [
                        {
                            "chatbot": "Support Bot",
                            "chatbot_id": "815e7ef4-3479-4689-ae6c-29ca1a04ca8e",
                            "data": {"name": "John", "timezone": "Africa/Johannesburg"},
                        },
                    ],
                },
            ),
        ],
    )
    def get(self, request):
        data_qs = ParticipantData.objects.select_related("experiment").filter(team=request.team)
        qs = Participant.objects.filter(team=request.team)
        if identifier := request.query_params.get("identifier"):
            qs = qs.filter(identifier=identifier)
        if platform := request.query_params.get("platform"):
            qs = qs.filter(platform=platform)
        if experiment_uuid := _parse_experiment_uuid(request.query_params.get("experiment")):
            data_qs = data_qs.filter(experiment__public_id=experiment_uuid)
            qs = qs.filter(id__in=data_qs.values_list("participant_id", flat=True))
        qs = qs.prefetch_related(Prefetch("data_set", queryset=data_qs, to_attr="_prefetched_participant_data"))
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = ParticipantDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="update_participant_data",
        summary="Update Participant Data",
        tags=["Participants"],
        request=ParticipantDataUpdateRequest(),
        responses={200: ParticipantDetailSerializer},
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


def _parse_experiment_uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as e:
        raise ValidationError({"experiment": "Must be a valid UUID."}) from e


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

    connect_data_by_experiment_id = {}
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
            connect_data_by_experiment_id[experiment.id] = participant_data

    if connect_data_by_experiment_id:
        _setup_connect_channels(identifier, connect_data_by_experiment_id)

    return Response(ParticipantDetailSerializer(participant).data)


def _get_participant_experiments(team, experiment_data) -> dict[str, Experiment]:
    experiment_ids = {data["experiment"] for data in experiment_data}
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}

    missing_ids = experiment_ids - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        raise NotFound(detail=response)

    return experiment_map


def _setup_connect_channels(identifier, participant_data_by_experiment_id):
    """
    Synchronously create Connect channels for experiments that use the ConnectMessaging channel,
    so the channel IDs can be returned in the response.

    Persists each new channel ID to ``ParticipantData.system_metadata``. Participant data that
    already has a channel ID is skipped, as are experiments without a Connect channel.
    """
    channels = ExperimentChannel.objects.filter(
        platform=ChannelPlatform.COMMCARE_CONNECT,
        experiment_id__in=participant_data_by_experiment_id.keys(),
    )
    # one channel per experiment, mirroring the previous task-based implementation
    channel_by_experiment_id = {channel.experiment_id: channel for channel in channels}
    pending = [
        (channel, participant_data_by_experiment_id[experiment_id])
        for experiment_id, channel in channel_by_experiment_id.items()
        if "commcare_connect_channel_id" not in participant_data_by_experiment_id[experiment_id].system_metadata
    ]
    if not pending:
        # Don't instantiate the client unnecessarily; it raises if Connect settings are missing.
        return

    connect_client = CommCareConnectClient()
    for channel, participant_data in pending:
        try:
            create_connect_channel_for_participant(channel, connect_client, identifier, participant_data)
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to create CommCare Connect channel for participant %s: HTTP %s - %s",
                identifier,
                e.response.status_code,
                e.response.text,
            )
            if e.response.status_code == 404:
                raise NotFound("Failed to create channel: Participant not found in CommCare Connect") from e
            elif e.response.status_code >= 500:
                raise ServiceUnavailable("Failed to create channel: CommCare Connect service error") from e
            raise BadRequest(f"Failed to create channel: {e.response.text}") from e
        except httpx.HTTPError as e:
            logger.error("Failed to create CommCare Connect channel for participant %s: %s", identifier, str(e))
            raise ServiceUnavailable("Failed to create channel: Unable to connect to CommCare Connect service") from e


def _schedule_external_id(data, experiment, participant):
    return data.get("id") or ScheduledMessage.generate_external_id(data["name"], experiment.id, participant.id)


def _apply_schedule_update(message, data):
    message.next_trigger_date = data["date"]
    message.custom_schedule_params["name"] = data["name"]
    message.custom_schedule_params["prompt_text"] = data["prompt"]
    return message


def _build_new_scheduled_message(request, experiment, participant, external_id, data):
    return ScheduledMessage(
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


@transaction.atomic()
def _create_update_schedules(request, experiment, participant, schedule_data):
    data_by_id = {
        _schedule_external_id(data, experiment, participant): data for data in schedule_data if not data.get("delete")
    }
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
            updated.append(_apply_schedule_update(existing_by_id[external_id], data))
        else:
            new.append(_build_new_scheduled_message(request, experiment, participant, external_id, data))

    delete_ids = {data["id"] for data in schedule_data if data.get("delete")}
    if delete_ids:
        ScheduledMessage.objects.filter(external_id__in=delete_ids).update(
            cancelled_at=timezone.now(), cancelled_by=request.user
        )
    if updated:
        ScheduledMessage.objects.bulk_update(updated, fields=["next_trigger_date", "custom_schedule_params"])
    if new:
        ScheduledMessage.objects.bulk_create(new)
