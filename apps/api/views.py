import json
import logging
import textwrap

import httpx
from django.conf import settings
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action, api_view, renderer_classes
from rest_framework.exceptions import NotFound
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.views import Request
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView, verify_hmac
from apps.api.serializers import (
    ExperimentSerializer,
    ExperimentSessionCreateSerializer,
    ExperimentSessionSerializer,
    ParticipantDataUpdateRequest,
    TriggerBotMessageRequest,
)
from apps.api.tasks import setup_connect_channels_for_bots, trigger_bot_message_task
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.files.models import File

logger = logging.getLogger(__name__)


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
    return _update_participant_data(request)


@extend_schema(exclude=True)
@api_view(["POST"])
@permission_required("experiments.change_participantdata")
def update_participant_data_old(request):
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
    if platform == ChannelPlatform.COMMCARE_CONNECT:
        # CommCare Connect identifiers are case-sensitive
        identifier = identifier.upper()

    team = request.team
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
            message = existing_by_id.get(external_id)
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
        queryset = ExperimentSession.objects.filter(team__slug=self.request.team.slug).all()
        if tags_query_param := self.request.query_params.get("tags"):
            queryset = queryset.filter(chat__tags__name__in=tags_query_param.split(","))
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


class BinaryRenderer(BaseRenderer):
    media_type = "application/octet-stream"
    format = "bin"


@extend_schema(operation_id="file_content", summary="Download File Content", tags=["Files"], responses=bytes)
@api_view(["GET"])
@renderer_classes([BinaryRenderer])
@permission_required("files.view_file")
def file_content_view(request, pk: int):
    file = get_object_or_404(File, id=pk, team=request.team)
    if not file.file:
        raise Http404()

    try:
        return FileResponse(file.file.open(), as_attachment=True, filename=file.file.name)
    except FileNotFoundError:
        raise Http404() from None


@csrf_exempt
@require_POST
def generate_key(request: Request):
    """Generates a key for a specific channel to use for secure communication"""
    token = request.META.get("HTTP_AUTHORIZATION")
    if not (token and "channel_id" in request.POST):
        return HttpResponse("Missing token or data", status=400)

    commcare_connect_channel_id = request.POST["channel_id"]
    response = httpx.get(settings.COMMCARE_CONNECT_GET_CONNECT_ID_URL, headers={"AUTHORIZATION": token})
    response.raise_for_status()
    connect_id = response.json().get("sub")

    try:
        participant_data = ParticipantData.objects.defer("data").get(
            participant__identifier=connect_id, system_metadata__commcare_connect_channel_id=commcare_connect_channel_id
        )
    except ParticipantData.DoesNotExist:
        raise Http404() from None

    if not participant_data.encryption_key:
        participant_data.generate_encryption_key()

    return JsonResponse({"key": participant_data.encryption_key})


@csrf_exempt
@require_POST
@verify_hmac
def callback(request: Request):
    """This callback endpoint is called by commcare connect when the message is delivered to the user"""
    # Not sure what to do with this, so just return
    return HttpResponse()


@csrf_exempt
@require_POST
@verify_hmac
def consent(request: Request):
    """The user gave consent to the bot to message them"""
    if not request.body:
        return HttpResponse("Missing data", status=400)
    request_data = json.loads(request.body)
    if "consent" not in request_data or "channel_id" not in request_data:
        return HttpResponse("Missing consent or commcare_connect_channel_id", status=400)

    participant_data = get_object_or_404(
        ParticipantData, system_metadata__commcare_connect_channel_id=request_data["channel_id"]
    )
    participant_data.update_consent(request_data["consent"])

    return HttpResponse()


@extend_schema(
    operation_id="trigger_bot_message",
    summary="Trigger the bot to send a message to the user",
    tags=["Channels"],
    request=TriggerBotMessageRequest(),
    responses={
        200: {},
        400: {"description": "Bad Request"},
        404: {"description": "Not Found"},
    },
    examples=[
        OpenApiExample(
            name="GenerateBotMessageAndSend",
            summary="Generates a bot message and sends it to the user",
            value={
                "identifier": "part1",
                "experiment": "exp1",
                "platform": "connect_messaging",
                "prompt_text": "Tell the user to do something",
            },
            status_codes=[200],
        ),
        OpenApiExample(
            name="ParticipantNotFound",
            summary="Participant not found",
            value={"detail": "Participant not found"},
            status_codes=[404],
        ),
        OpenApiExample(
            name="ExperimentChannelNotFound",
            summary="Experiment cannot send messages on the specified channel",
            value={"detail": "Experiment cannot send messages on the connect_messaging channel"},
            status_codes=[404],
        ),
        OpenApiExample(
            name="ConsentNotGiven",
            summary="User has not given consent",
            value={"detail": "User has not given consent"},
            status_codes=[400],
        ),
    ],
)
@api_view(["POST"])
def trigger_bot_message(request):
    """
    Trigger the bot to send a message to the user
    """
    serializer = TriggerBotMessageRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.data
    platform = data["platform"]
    if platform == ChannelPlatform.COMMCARE_CONNECT:
        # CommCare Connect identifiers are case-sensitive
        data["identifier"] = data["identifier"].upper()

    identifier = data["identifier"]
    experiment_public_id = data["experiment"]

    experiment = get_object_or_404(Experiment, public_id=experiment_public_id, team=request.team)

    participant_data = ParticipantData.objects.filter(
        participant__identifier=identifier,
        participant__platform=platform,
        experiment=experiment.id,
    ).first()
    if platform == ChannelPlatform.COMMCARE_CONNECT and not participant_data:
        # The commcare_connect channel requires certain data from the participant_data in order to send messages to th
        # user, which is why we need to check if the participant_data exists
        return JsonResponse({"detail": "Participant not found"}, status=status.HTTP_404_NOT_FOUND)
    elif not Participant.objects.filter(identifier=identifier, platform=platform).exists():
        return JsonResponse({"detail": "Participant not found"}, status=status.HTTP_404_NOT_FOUND)

    if not ExperimentChannel.objects.filter(platform=platform, experiment=experiment).exists():
        return JsonResponse(
            {"detail": f"Experiment cannot send messages on the {platform} channel"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if platform == ChannelPlatform.COMMCARE_CONNECT and not participant_data.has_consented():
        return JsonResponse({"detail": "User has not given consent"}, status=status.HTTP_400_BAD_REQUEST)

    trigger_bot_message_task.delay(data)

    return Response(status=status.HTTP_200_OK)
