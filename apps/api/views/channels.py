import json
import logging

import httpx
from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView, Request

from apps.api.permissions import verify_hmac
from apps.api.serializers import TriggerBotMessageRequest, TriggerBotMessageResponse
from apps.api.tasks import (
    DuplicateConnectChannelError,
    connect_channel_error_details,
    create_connect_channel_for_participant,
    trigger_bot_message_task,
)
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.registry import get_channel_class_for_platform
from apps.chatbots.version_resolver import resolve_published_or_working
from apps.experiments.models import Experiment, Participant, ParticipantData
from apps.teams.utils import current_team

connect_logger = logging.getLogger("api.connect_channel")


@csrf_exempt
@require_POST
def generate_key(request: Request):
    """Generates a key for a specific channel to use for secure communication"""
    token = request.META.get("HTTP_AUTHORIZATION")
    if not (token and "channel_id" in request.POST):
        return HttpResponse("Missing token or data", status=400)

    commcare_connect_channel_id = request.POST["channel_id"]
    response = httpx.get(settings.COMMCARE_CONNECT_GET_CONNECT_ID_URL, headers={"AUTHORIZATION": token})
    connect_logger.info(f"CommCare Connect response: {response.status_code}")
    response.raise_for_status()
    connect_id = response.json().get("sub").lower()

    participant_data = ParticipantData.objects.for_connect_channel(
        commcare_connect_channel_id, participant_identifier=connect_id
    )
    if participant_data is None:
        connect_logger.error(
            f"ParticipantData with connect_id: {connect_id} and channel_id: {commcare_connect_channel_id} not found"
        )
        raise Http404()

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

    participant_data = ParticipantData.objects.for_connect_channel(request_data["channel_id"])
    if participant_data is None:
        raise Http404()
    participant_data.update_consent(request_data["consent"])

    return HttpResponse()


def _get_or_create_participant_data(request, identifier, platform, experiment, incoming_participant_data):
    """Get or create a participant and their ParticipantData for an experiment.

    If ``incoming_participant_data`` is provided it is merged into any existing data.
    Returns the (possibly newly created) ``ParticipantData`` instance.
    """
    participant_data = ParticipantData.objects.filter(
        participant__identifier=identifier,
        participant__platform=platform,
        experiment=experiment.id,
    ).first()

    if not participant_data:
        participant, _ = Participant.objects.get_or_create(identifier=identifier, platform=platform, team=request.team)
        participant_data, created = ParticipantData.objects.get_or_create(
            participant=participant,
            experiment=experiment,
            defaults={"team": request.team, "data": incoming_participant_data or {}},
        )
        if not created and incoming_participant_data:
            merged_data = {**participant_data.data, **incoming_participant_data}
            if merged_data != participant_data.data:
                participant_data.data = merged_data
                participant_data.save(update_fields=["data"])
    elif incoming_participant_data:
        merged_data = {**participant_data.data, **incoming_participant_data}
        if merged_data != participant_data.data:
            participant_data.data = merged_data
            participant_data.save(update_fields=["data"])

    return participant_data


def _ensure_commcare_connect_ready(channel, identifier, participant_data):
    """Ensure a CommCare Connect channel exists for the participant and that they have consented.

    Returns a ``JsonResponse`` error response if the channel cannot be created or consent has
    not been given, or ``None`` if everything is in order.
    """
    if not participant_data.system_metadata.get("commcare_connect_channel_id"):
        connect_client = CommCareConnectClient()
        try:
            create_connect_channel_for_participant(channel, connect_client, identifier, participant_data)
        except (DuplicateConnectChannelError, httpx.HTTPError) as e:
            status_code, detail = connect_channel_error_details(e, identifier)
            return JsonResponse({"detail": detail}, status=status_code)

    if not participant_data.has_consented():
        return JsonResponse({"detail": "User has not given consent"}, status=status.HTTP_400_BAD_REQUEST)

    return None


def handle_trigger_bot_message(request, response_serializer_class):
    """Run the trigger-bot flow shared by all API versions and build the response.

    Validates the request, resolves the experiment/channel, ensures CommCare Connect enrollment and
    consent where relevant, creates or reuses the session, and dispatches the async bot-message task.
    The session is then serialised with ``response_serializer_class`` (which differs per API version).

    Returns the final response to hand back from the view: a 200 ``Response`` on success, or an error
    response (bad channel, failed enrollment, missing consent) to return as-is.
    """
    serializer = TriggerBotMessageRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.data
    platform = data["platform"]
    identifier = ChannelPlatform(platform).normalize_identifier(data["identifier"])
    # Propagate the normalized identifier so the async task uses a consistent value
    data = dict(data)
    data["identifier"] = identifier
    experiment = get_object_or_404(Experiment, public_id=data["experiment"], team=request.team)

    channel = ExperimentChannel.objects.filter(platform=platform, experiment=experiment).first()
    if not channel:
        return JsonResponse(
            {"detail": f"Experiment cannot send messages on the {platform} channel. Create the channel first."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    participant_data = _get_or_create_participant_data(
        request, identifier, platform, experiment, data.get("participant_data")
    )

    if platform == ChannelPlatform.COMMCARE_CONNECT:
        if error := _ensure_commcare_connect_ready(channel, identifier, participant_data):
            return error

    target_experiment = resolve_published_or_working(experiment)
    ChannelClass = get_channel_class_for_platform(platform)
    bot_channel = ChannelClass(experiment=target_experiment, experiment_channel=channel)
    with current_team(experiment.team):
        bot_channel.ensure_session_exists_for_participant(identifier, new_session=data["start_new_session"])
        session = bot_channel.experiment_session
        assert session is not None
        if data.get("session_data"):
            session.state = {**session.state, **data["session_data"]}
            session.save(update_fields=["state"])

    trigger_bot_message_task.delay_on_commit(
        str(session.external_id), data.get("prompt_text"), data.get("message_text")
    )

    response_serializer = response_serializer_class(
        instance=session, context={"request": request, "participant_data": participant_data}
    )
    return Response(response_serializer.data, status=status.HTTP_200_OK)


class TriggerBotMessageView(APIView):
    required_scopes = ("chatbots:interact",)

    @extend_schema(
        operation_id="trigger_bot_message",
        summary="Trigger the bot to send a message to the user, or deliver a message directly",
        tags=["Channels"],
        request=TriggerBotMessageRequest(),
        responses={
            200: TriggerBotMessageResponse,
            400: {"description": "Bad Request"},
            404: {"description": "Not Found"},
        },
        examples=[
            OpenApiExample(
                name="GenerateBotMessageAndSend",
                summary="Generates a bot message and sends it to the user (auto-creates participant if needed).",
                value={
                    "identifier": "+15556793",
                    "experiment": "exp1",
                    "platform": "whatsapp",
                    "prompt_text": "Tell the user to do something",
                    "session_data": {"key": "value"},
                    "participant_data": {"key": "value"},
                },
                status_codes=[200],
            ),
            OpenApiExample(
                name="SendMessageDirectly",
                summary="Send a pre-written message directly to the participant, bypassing the bot/LLM.",
                value={
                    "identifier": "+15556793",
                    "experiment": "exp1",
                    "platform": "whatsapp",
                    "message_text": "Your appointment is confirmed for tomorrow at 10am.",
                    "session_data": {"key": "value"},
                    "participant_data": {"key": "value"},
                },
                status_codes=[200],
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
    @transaction.atomic
    def post(self, request):
        """
        Trigger the bot to send a message to the user, or deliver a message directly.

        Provide either ``prompt_text`` (routes through the LLM/bot pipeline) or ``message_text``
        (sends the exact text to the participant without any LLM processing). Exactly one is required.
        """
        return handle_trigger_bot_message(request, TriggerBotMessageResponse)
