import json
import uuid

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.api.permissions import HasUserAPIKey
from apps.channels import tasks
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment


@csrf_exempt
def new_telegram_message(request, channel_external_id: uuid):
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.TELEGRAM_SECRET_TOKEN:
        return HttpResponseBadRequest("Invalid request.")

    data = json.loads(request.body)
    tasks.handle_telegram_message.delay(message_data=data, channel_external_id=channel_external_id)
    return HttpResponse()


@csrf_exempt
@require_POST
def new_twilio_message(request):
    message_data = json.dumps(request.POST.dict())
    tasks.handle_twilio_message.delay(message_data)
    return HttpResponse()


@csrf_exempt
@require_POST
def new_sureadhere_message(request, channel_external_id: uuid):
    message_data = json.loads(request.body)
    tasks.handle_sureadhere_message.delay(channel_external_id=channel_external_id, message_data=message_data)
    return HttpResponse()


@csrf_exempt
def new_turn_message(request, experiment_id: uuid):
    message_data = json.loads(request.body.decode("utf-8"))
    if "messages" not in message_data:
        # Normal inbound messages should have a "messages" key, so ignore everything else
        return HttpResponse()

    tasks.handle_turn_message.delay(experiment_id=experiment_id, message_data=message_data)
    return HttpResponse()


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
def new_api_message(request, experiment_id: uuid):
    """
    Expected body: {"message": ""}
    """
    message_data = request.data.copy()
    message_data["participant_id"] = request.user.email
    experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    experiment_channel, _created = ExperimentChannel.objects.get_or_create(
        name=f"{experiment.id}-api",
        experiment=experiment,
        platform=ChannelPlatform.API,
    )
    response = tasks.handle_api_message(experiment_channel, message_data=message_data)
    return Response(data={"response": response})
