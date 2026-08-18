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

from apps.api.permissions import (
    CanTriggerBotMessage,
    IsAuthenticatedOrMachineToken,
    ReadOnlyAPIKeyPermission,
    verify_hmac,
)
from apps.api.serializers import TriggerBotMessageRequest, TriggerBotMessageResponse
from apps.api.tasks import trigger_bot_message_task
from apps.api.trigger_bot import TriggerBotMessageError, prepare_trigger_bot_message
from apps.experiments.models import Experiment, ParticipantData
from apps.oauth.permissions import TokenHasOAuthScope, enforce_application_chatbot_access
from apps.utils.rate_limit import rate_limited

connect_logger = logging.getLogger("api.connect_channel")


@csrf_exempt
@require_POST
@rate_limited("credentials")
def generate_key(request: Request):
    """Generates a key for a specific channel to use for secure communication.

    Counted at the door by client IP, ahead of the outbound call below. The view
    accepts any non-empty Authorization header and lets CommCare Connect decide
    whether it was valid, so an unauthenticated caller can make us issue that request.
    `channel_id` arrives before the call but the caller supplies it, so keying on it
    would let someone drain a known channel's budget or evade the counter by varying it.
    """
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
        connect_logger.warning(
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


def handle_trigger_bot_message(request, response_serializer_class):
    """Run the trigger-bot flow shared by all API versions and build the response.

    Validates the request, resolves the experiment, then hands off to ``prepare_trigger_bot_message``
    (channel, CommCare Connect enrollment and consent, session) before dispatching the async
    bot-message task. The session is then serialised with ``response_serializer_class`` (which
    differs per API version).

    Returns the final response to hand back from the view: a 200 ``Response`` on success, or an error
    response (bad or disabled channel, failed enrollment, missing consent) to return as-is.
    """
    serializer = TriggerBotMessageRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.data
    experiment = get_object_or_404(Experiment, public_id=data["experiment"], team=request.team)
    # Before prepare_trigger_bot_message below, which creates participant data as a side effect.
    enforce_application_chatbot_access(request, experiment)

    # Returning (rather than raising) keeps the atomic block committing what got as far as being
    # created -- the Connect auto-consent flow relies on the participant data surviving the error.
    try:
        session, participant_data = prepare_trigger_bot_message(
            experiment,
            data["identifier"],
            data["platform"],
            start_new_session=data["start_new_session"],
            session_data=data.get("session_data"),
            incoming_participant_data=data.get("participant_data"),
        )
    except TriggerBotMessageError as error:
        return JsonResponse({"detail": error.detail}, status=error.status_code)

    trigger_bot_message_task.delay_on_commit(
        str(session.external_id), data.get("prompt_text"), data.get("message_text")
    )

    response_serializer = response_serializer_class(
        instance=session, context={"request": request, "participant_data": participant_data}
    )
    return Response(response_serializer.data, status=status.HTTP_200_OK)


class TriggerBotMessageView(APIView):
    # Spelled out rather than inherited from DEFAULT_PERMISSION_CLASSES so the role check is explicit:
    # the default stack gates on team membership and the OAuth scope only, which for API-key callers
    # left this endpoint open to any member regardless of their role.
    permission_classes = [
        IsAuthenticatedOrMachineToken,
        ReadOnlyAPIKeyPermission,
        CanTriggerBotMessage,
        TokenHasOAuthScope,
    ]
    required_scopes = ("chatbots:interact",)

    @extend_schema(
        operation_id="trigger_bot_message",
        summary="Trigger the bot to send a message to the user, or deliver a message directly",
        tags=["Channels"],
        request=TriggerBotMessageRequest(),
        responses={
            200: TriggerBotMessageResponse,
            400: {"description": "Bad Request"},
            403: {"description": "The OAuth application is not authorized for this chatbot"},
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
