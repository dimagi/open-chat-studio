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
from apps.api.serializers import TriggerBotMessageRequest
from apps.api.tasks import trigger_bot_message_task
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment, Participant, ParticipantData

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

    try:
        participant_data = ParticipantData.objects.defer("data").get(
            participant__identifier=connect_id, system_metadata__commcare_connect_channel_id=commcare_connect_channel_id
        )
    except ParticipantData.DoesNotExist:
        connect_logger.exception(
            f"ParticipantData with connect_id: {connect_id} and channel_id: {commcare_connect_channel_id} not found"
        )
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


class TriggerBotMessageView(APIView):
    required_scopes = ("chatbots:interact",)

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
    @transaction.atomic
    def post(self, request):
        """
        Trigger the bot to send a message to the user
        """
        serializer = TriggerBotMessageRequest(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.data
        platform = data["platform"]
        identifier = data["identifier"]
        identifier = ChannelPlatform(platform).normalize_identifier(identifier)
        experiment_public_id = data["experiment"]

        experiment = get_object_or_404(Experiment, public_id=experiment_public_id, team=request.team)

        channel = ExperimentChannel.objects.filter(platform=platform, experiment=experiment).first()
        if not channel:
            return JsonResponse(
                {"detail": f"Experiment cannot send messages on the {platform} channel. Create the channel first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        participant_data = ParticipantData.objects.filter(
            participant__identifier=identifier,
            participant__platform=platform,
            experiment=experiment.id,
        ).first()

        if not participant_data:
            participant, _ = Participant.objects.get_or_create(
                identifier=identifier, platform=platform, team=request.team
            )
            participant_data, created = ParticipantData.objects.get_or_create(
                participant=participant,
                experiment=experiment,
                defaults={"team": request.team, "data": data.get("participant_data") or {}},
            )
            # If participant_data already existed and participant_data is provided in request
            if not created and data.get("participant_data"):
                merged_data = {**participant_data.data, **data["participant_data"]}
                if merged_data != participant_data.data:
                    participant_data.data = merged_data
                    participant_data.save(update_fields=["data"])
        elif data.get("participant_data"):
            merged_data = {**participant_data.data, **data["participant_data"]}
            if merged_data != participant_data.data:
                participant_data.data = merged_data
                participant_data.save(update_fields=["data"])

        if platform == ChannelPlatform.COMMCARE_CONNECT:
            if not participant_data.system_metadata.get("commcare_connect_channel_id"):
                # Trigger the setup task to create the channel and get consent status from CCC
                from apps.api.tasks import create_connect_channel_for_participant
                from apps.channels.clients.connect_client import CommCareConnectClient

                connect_client = CommCareConnectClient()
                try:
                    create_connect_channel_for_participant(channel, connect_client, identifier, participant_data)
                except httpx.HTTPStatusError as e:
                    connect_logger.error(
                        f"Failed to create CommCare Connect channel for participant {identifier}: "
                        f"HTTP {e.response.status_code} - {e.response.text}"
                    )
                    if e.response.status_code == 404:
                        return JsonResponse(
                            {"detail": "Failed to create channel: Participant not found in CommCare Connect"},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    elif e.response.status_code >= 500:
                        return JsonResponse(
                            {"detail": "Failed to create channel: CommCare Connect service error"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE,
                        )
                    else:
                        return JsonResponse(
                            {"detail": f"Failed to create channel: {e.response.text}"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                except httpx.HTTPError as e:
                    connect_logger.error(
                        f"Failed to create CommCare Connect channel for participant {identifier}: {str(e)}"
                    )
                    return JsonResponse(
                        {"detail": "Failed to create channel: Unable to connect to CommCare Connect service"},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

            if not participant_data.has_consented():
                return JsonResponse({"detail": "User has not given consent"}, status=status.HTTP_400_BAD_REQUEST)

        trigger_bot_message_task.delay(data)

        return Response(status=status.HTTP_200_OK)
