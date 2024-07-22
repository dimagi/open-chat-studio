import json
import uuid

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.channels import tasks
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import ExperimentSession


@csrf_exempt
def new_telegram_message(request, channel_external_id: uuid):
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.TELEGRAM_SECRET_TOKEN:
        return HttpResponseBadRequest("Invalid request.")

    data = json.loads(request.body)
    tasks.handle_telegram_message.delay(message_data=data, channel_external_id=channel_external_id)
    return HttpResponse()


@csrf_exempt
def new_twilio_message(request):
    message_data = json.dumps(request.POST.dict())
    tasks.handle_twilio_message.delay(
        message_data=message_data,
        request_uri=request.build_absolute_uri(),
        signature=request.headers.get("X-Twilio-Signature"),
    )
    return HttpResponse()


@csrf_exempt
def new_turn_message(request, experiment_id: uuid):
    message_data = json.loads(request.body.decode("utf-8"))
    if "messages" not in message_data:
        # Normal inbound messages should have a "messages" key, so ignore everything else
        return HttpResponse()

    tasks.handle_turn_message.delay(experiment_id=experiment_id, message_data=message_data)
    return HttpResponse()


@extend_schema(
    operation_id="new_api_message",
    summary="New API Message",
    tags=["Channels"],
    request=inline_serializer(
        "NewAPIMessage",
        fields={
            "message": serializers.CharField(label="User message"),
            "session": serializers.CharField(required=False, label="Optional session ID"),
        },
    ),
    responses={
        200: inline_serializer(
            "NewAPIMessageResponse",
            fields={
                "response": serializers.CharField(label="AI response"),
            },
        )
    },
    parameters=[
        OpenApiParameter(
            name="experiment_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Experiment ID",
        ),
    ],
)
@api_view(["POST"])
def new_api_message(request, experiment_id: uuid):
    """Chat with an experiment."""
    message_data = request.data.copy()
    participant_id = request.user.email

    session = None
    if session_id := message_data.get("session"):
        try:
            session = ExperimentSession.objects.select_related("experiment_channel").get(
                external_id=session_id,
                experiment__public_id=experiment_id,
                team=request.team,
                participant__user=request.user,
                experiment_channel__platform=ChannelPlatform.API,
            )
        except ExperimentSession.DoesNotExist:
            raise Http404

        participant_id = session.participant.identifier
        experiment_channel = session.experiment_channel
    else:
        experiment_channel, _ = ExperimentChannel.objects.get_or_create(
            name=f"{experiment_id}-api",
            experiment_id=experiment_id,
            platform=ChannelPlatform.API,
        )

    response = tasks.handle_api_message(
        request.user,
        experiment_channel,
        message_data["message"],
        participant_id,
        session,
    )
    return Response(data={"response": response})
