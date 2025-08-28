import json
import uuid

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.api.permissions import verify_hmac
from apps.channels import tasks
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.serializers import (
    ApiMessageSerializer,
    ApiResponseMessageSerializer,
    CommCareConnectMessageSerializer,
)
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData


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
    tasks.handle_twilio_message.delay(
        message_data=message_data,
        request_uri=request.build_absolute_uri(),
        signature=request.headers.get("X-Twilio-Signature"),
    )
    return HttpResponse()


@csrf_exempt
@require_POST
def new_sureadhere_message(request, sureadhere_tenant_id: int):
    message_data = json.loads(request.body)
    tasks.handle_sureadhere_message.delay(sureadhere_tenant_id=sureadhere_tenant_id, message_data=message_data)
    return HttpResponse()


@csrf_exempt
def new_turn_message(request, experiment_id: uuid):
    message_data = json.loads(request.body.decode("utf-8"))
    if "messages" not in message_data:
        # Normal inbound messages should have a "messages" key, so ignore everything else
        return HttpResponse()

    tasks.handle_turn_message.delay(experiment_id=experiment_id, message_data=message_data)
    return HttpResponse()


def new_api_message_schema(versioned: bool):
    operation_id = "new_api_message"
    summary = "New API Message"
    parameters = [
        OpenApiParameter(
            name="experiment_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Experiment ID",
        ),
    ]

    if versioned:
        operation_id = f"{operation_id}_versioned"
        summary = "New API Message Versioned"
        parameters.append(
            OpenApiParameter(
                name="version",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Version of experiment",
            )
        )

    return extend_schema(
        operation_id=operation_id,
        summary=summary,
        tags=["Channels"],
        request=ApiMessageSerializer(),
        responses={200: ApiResponseMessageSerializer()},
        parameters=parameters,
    )


@new_api_message_schema(versioned=False)
@api_view(["POST"])
def new_api_message(request, experiment_id: uuid):
    return _new_api_message(request, experiment_id)


@new_api_message_schema(versioned=True)
@api_view(["POST"])
def new_api_message_versioned(request, experiment_id: uuid, version=None):
    return _new_api_message(request, experiment_id, version)


def _new_api_message(request, experiment_id: uuid, version=None):
    """Chat with an experiment."""
    message_data = request.data.copy()
    participant_id = request.user.email

    session = None
    if session_id := message_data.get("session"):
        try:
            experiment = Experiment.objects.get(public_id=experiment_id)
            session = ExperimentSession.objects.select_related("experiment", "experiment_channel").get(
                external_id=session_id,
                experiment=experiment,
                team=request.team,
                participant__user=request.user,
                experiment_channel__platform=ChannelPlatform.API,
            )
        except ExperimentSession.DoesNotExist:
            raise Http404() from None
        participant_id = session.participant.identifier
        experiment_channel = session.experiment_channel
        experiment = session.experiment
    else:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
        experiment_channel = ExperimentChannel.objects.get_team_api_channel(request.team)
    experiment_version = experiment.get_version(version) if version is not None else experiment.default_version
    ai_response = tasks.handle_api_message(
        request.user,
        experiment_version,
        experiment_channel,
        message_data["message"],
        participant_id,
        session,
    )

    attachments = []
    if attached_files := ai_response.get_attached_files():
        attachments = [
            {"file_name": file.name, "link": file.download_link(ai_response.chat.experiment_session.id)}
            for file in attached_files
        ]

    return Response(
        data={
            "response": ai_response.content,
            "attachments": attachments,
        }
    )


@require_POST
@csrf_exempt
@verify_hmac
def new_connect_message(request: HttpRequest):
    serializer = CommCareConnectMessageSerializer(data=json.loads(request.body))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    connect_channel_id = serializer.data["channel_id"]
    try:
        participant_data = ParticipantData.objects.get(
            system_metadata__commcare_connect_channel_id=connect_channel_id,
        )

        channel = ExperimentChannel.objects.get(
            platform=ChannelPlatform.COMMCARE_CONNECT, experiment__id=participant_data.experiment_id
        )
    except ParticipantData.DoesNotExist:
        return JsonResponse({"detail": "No participant data found"}, status=404)
    except ExperimentChannel.DoesNotExist:
        return JsonResponse({"detail": "No experiment channel found"}, status=404)

    if not participant_data.has_consented():
        return JsonResponse({"detail": "User has not given consent"}, status=status.HTTP_400_BAD_REQUEST)

    tasks.handle_commcare_connect_message.delay(
        experiment_channel_id=channel.id, participant_data_id=participant_data.id, messages=serializer.data["messages"]
    )
    return HttpResponse()


@require_GET
def channel_edit_dialog(request, team_slug, experiment_id, channel_id):
    channel = get_object_or_404(
        ExperimentChannel, id=channel_id, experiment__id=experiment_id, experiment__team__slug=team_slug
    )
    form = channel.form
    extra_form = channel.extra_form
    context = {
        "request": request,
        "team": request.team,
        "experiment": channel.experiment,
        "channel": channel,
        "form": form,
        "extra_form": extra_form,
    }

    return render(request, "chatbots/partials/channel_edit_dialog.html", context)


@require_GET
def channel_create_dialog(request, team_slug, experiment_id, platform_value):
    experiment = get_object_or_404(Experiment, id=experiment_id, team__slug=team_slug)

    channels = experiment.experimentchannel_set.exclude(
        platform__in=[ChannelPlatform.WEB, ChannelPlatform.API, ChannelPlatform.EVALUATIONS]
    ).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)

    platform_forms = {}
    form_kwargs = {"experiment": experiment}
    for platform in available_platforms:
        if platform.form:
            platform_forms[platform] = platform.form(**form_kwargs)
    try:
        platform_enum = ChannelPlatform(platform_value)
    except ValueError:
        raise Http404("Platform not found.")

    platform_form = platform_forms.get(platform_enum)
    extra_form = platform_enum.extra_form()
    if not platform_form:
        return HttpResponse("Invalid or unavailable platform.", status=400)

    context = {
        "request": request,
        "team": request.team,
        "experiment": experiment,
        "platform": platform_enum,
        "platform_form": platform_form,
        "extra_form": extra_form,
    }
    return render(request, "chatbots/partials/channel_create_dialog.html", context)
