import json
import logging

from asgiref.sync import sync_to_async
from django.http import JsonResponse
from django.shortcuts import aget_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.response import Response

from apps.api.auth import ahandle_embedded_widget_auth
from apps.api.exceptions import EmbeddedWidgetAuthError
from apps.api.serializers import (
    ChatSendMessageRequest,
    ChatStartSessionRequest,
    ChatStartSessionResponse,
)
from apps.channels.datamodels import Attachment
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks_async import handle_api_message_async
from apps.chat.channels import ApiChannel
from apps.chat.models import Chat, ChatAttachment
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.files.models import File

logger = logging.getLogger("ocs.api_chat")


async def check_experiment_access(experiment, participant_id):
    """
    Check if the request has access to the experiment based on public API settings.

    Returns:
        Response object if access denied, None if access allowed
    """
    if experiment.is_public:
        return None

    if not participant_id:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    if not await experiment.ais_participant_allowed(participant_id):
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    return None


async def check_session_access(session, request):
    """
    Check if the request has access to the session.
    Now handles both authenticated users and embedded widgets.
    Args:
        session: Session object (should have experiment_channel prefetched)
        request: Request object (required for embedded widgets)

    Note:
        Callers should use select_related('experiment_channel') when querying
        sessions to avoid N+1 queries.

    Returns:
        Response object if access denied, None if access allowed
    """
    if session.experiment_channel.platform == ChannelPlatform.EMBEDDED_WIDGET:
        try:
            experiment_channel = await ahandle_embedded_widget_auth(request, session=session)
            if experiment_channel != session.experiment_channel:
                logging.error("Channel mismatch in embedded widget auth")
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            return None  # Access allowed
        except EmbeddedWidgetAuthError:
            logger.error("Permission denied during embedded widget authentication")
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
        except Exception:
            logger.exception("Error during embedded widget authentication")
            return Response({"error": "Embedded widget authentication failed"}, status=status.HTTP_403_FORBIDDEN)
    return await check_experiment_access(session.experiment, session.participant.identifier)


@csrf_exempt
async def achat_start_session(request):
    """Start a new chat session - supports both authenticated users and embedded widgets"""
    data = request.POST
    if not data:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
    serializer = ChatStartSessionRequest(data=data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    experiment_id = data["chatbot_id"]
    session_data = data.get("session_data", {})
    remote_id = data.get("participant_remote_id", "")
    name = data.get("participant_name")
    try:
        experiment_channel = await ahandle_embedded_widget_auth(request, experiment_id=experiment_id)
    except EmbeddedWidgetAuthError as e:
        return Response({"error": e.message}, status=status.HTTP_403_FORBIDDEN)

    # Get experiment
    experiment = await aget_object_or_404(Experiment.objects.select_related("team"), public_id=experiment_id)
    if not experiment.is_working_version:
        return Response(
            {"error": "Chatbot ID must reference the unreleased version of an chatbot"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    team = experiment.team

    if not experiment_channel:
        # legacy flow
        experiment_channel = await ExperimentChannel.objects.aget_team_api_channel(team)

    if request.user.is_authenticated:
        user = request.user
        participant_id = user.email
        # Enforce this for authenticated users
        # Currently this only happens if the chat widget is being hosted on the same OCS instance as the bot
        if remote_id != participant_id:
            return Response({"error": "Remote ID must match your email address"}, status=status.HTTP_400_BAD_REQUEST)
        remote_id = ""
    else:
        user = None
        participant_id = None

    # Create or get participant
    if user is not None:
        participant, created = await Participant.objects.aget_or_create(
            identifier=participant_id,
            team=team,
            platform=experiment_channel.platform,
            defaults={"user": user, "remote_id": remote_id},
        )
    else:
        participant = await Participant.acreate_anonymous(team, experiment_channel.platform, remote_id)

    if name:
        if participant.name != name:
            participant.name = name
            await participant.asave(update_fields=["name"])
        participant_data, _ = await ParticipantData.objects.aget_or_create(
            participant=participant, experiment=experiment, team=team, defaults={"data": {}}
        )
        if participant_data.data.get("name") != name:
            participant_data.data["name"] = name
            await participant_data.asave(update_fields=["data"])

    metadata = {Chat.MetadataKeys.EMBED_SOURCE: request.headers.get("referer", None)}

    session = await sync_to_async(ApiChannel.start_new_session, thread_sensitive=True)(
        working_experiment=experiment,
        experiment_channel=experiment_channel,
        participant_identifier=participant.identifier,
        participant_user=user,
        metadata=metadata,
    )
    if user is not None and session_data:
        session.state = session_data
        await session.asave(update_fields=["state"])

    # Prepare response data
    response_data = {
        "session_id": session.external_id,
        "chatbot": experiment,
        "participant": participant,
    }

    serialized_response = ChatStartSessionResponse(response_data, context={"request": request})
    response_data = await sync_to_async(serialize_async, thread_sensitive=True)(serialized_response)
    return JsonResponse(response_data, status=status.HTTP_201_CREATED)


def serialize_async(serializer):
    return serializer.data


class ChatSendMessageRequestWithAttachments(ChatSendMessageRequest):
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, help_text="List of file IDs from prior upload"
    )


@csrf_exempt
async def achat_send_message(request, session_id):
    """
    Send a message to a chat session
    """
    data = request.POST
    if not data:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
    serializer = ChatSendMessageRequestWithAttachments(data=data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    message_text = data["message"]
    attachment_ids = data.get("attachment_ids", [])

    session = await aget_object_or_404(
        ExperimentSession.objects.select_related("experiment_channel", "experiment", "participant", "experiment__team"),
        external_id=session_id,
    )

    if access_response := await check_session_access(session, request):
        return access_response

    # Verify session is active
    if session.is_complete:
        return Response({"error": "Session has ended"}, status=status.HTTP_400_BAD_REQUEST)

    attachment_data = []
    if attachment_ids:
        files = File.objects.filter(id__in=attachment_ids, team=session.team)

        if await files.acount() != len(attachment_ids):
            return Response({"error": "One or more file IDs are invalid"}, status=status.HTTP_400_BAD_REQUEST)
        await files.aupdate(expiry_date=None)
        chat_attachment, created = await ChatAttachment.objects.aget_or_create(
            chat=session.chat,
            tool_type="ocs_attachments",
        )
        files_list = [file async for file in files]
        await chat_attachment.files.aadd(*files_list)
        for file_obj in files_list:
            attachment = Attachment.from_file(file_obj, type="ocs_attachments", session_id=session.id)
            attachment_data.append(attachment.model_dump())

    # Queue the response generation as a background task
    experiment_version = await Experiment.objects.aget_default_or_working(session.experiment)

    # await sync_to_async(prefetch_related_objects, thread_sensitive=True)(
    #     [experiment_version], "team", "pipeline", "trace_provider"
    # )
    #
    # response = await ahandle_api_message(
    #     request.user,
    #     experiment_version,
    #     session.experiment_channel,
    #     message_text,
    #     participant_id=session.participant.identifier,
    #     session=session,
    # )

    result = handle_api_message_async.kiq(
        request.user.id,
        experiment_version.id,
        session.experiment_channel.id,
        message_text,
        participant_id=session.participant.identifier,
        session=session.id,
    )

    task_result = await result.wait_result(timeout=30)
    return JsonResponse({"message": task_result.return_value}, status=status.HTTP_200_OK)
