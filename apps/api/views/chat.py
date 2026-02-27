import logging
import pathlib

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.api.authentication import EmbeddedWidgetAuthentication
from apps.api.permissions import LegacySessionAccessPermission, WidgetDomainPermission
from apps.api.serializers import (
    ChatPollResponse,
    ChatSendMessageRequest,
    ChatSendMessageResponse,
    ChatStartSessionRequest,
    ChatStartSessionResponse,
    MessageSerializer,
)
from apps.channels.datamodels import Attachment
from apps.channels.models import ExperimentChannel
from apps.channels.utils import get_experiment_session_cached
from apps.chat.channels import ApiChannel
from apps.chat.models import Chat, ChatAttachment, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, Participant, ParticipantData
from apps.experiments.task_utils import get_message_task_response
from apps.experiments.tasks import get_response_for_webchat_task
from apps.files.models import File
from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput

AUTH_CLASSES = [SessionAuthentication, EmbeddedWidgetAuthentication]
SESSION_PERMISSION_CLASSES = [WidgetDomainPermission, LegacySessionAccessPermission]

MAX_FILE_SIZE_MB = settings.MAX_FILE_SIZE_MB
MAX_TOTAL_SIZE_MB = 50
SUPPORTED_FILE_EXTENSIONS = settings.SUPPORTED_FILE_TYPES["collections"]

logger = logging.getLogger("ocs.api_chat")


def validate_file_upload(file):
    """
    Validate a file upload for size and type restrictions.
    Returns: tuple: (is_valid, error_message)
    """
    file_size_mb = file.size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        return False, f"File '{file.name}' exceeds maximum size of {MAX_FILE_SIZE_MB}MB"
    file_ext = pathlib.Path(file.name).suffix.lower()
    mime_type = file.content_type or ""
    content_type = mime_type.split("/")[0]
    # All text files are allowed
    if content_type != "text" and file_ext not in SUPPORTED_FILE_EXTENSIONS:
        return False, f"File type '{file_ext}' is not supported. Allowed types: {', '.join(SUPPORTED_FILE_EXTENSIONS)}"
    return True, None


@extend_schema(
    operation_id="chat_upload_file",
    summary="Upload files for a chat message",
    tags=["Chat"],
    request=inline_serializer(
        "ChatUploadFileRequest",
        {
            "files": serializers.ListField(
                child=serializers.FileField(),
                help_text=f"Files to upload (max {MAX_TOTAL_SIZE_MB}MB each, {MAX_TOTAL_SIZE_MB}MB total)",
            )
        },
    ),
    responses={
        201: inline_serializer(
            "ChatUploadFileResponse",
            {
                "files": inline_serializer(
                    "UploadedFile",
                    {
                        "id": serializers.IntegerField(),
                        "name": serializers.CharField(),
                        "size": serializers.IntegerField(),
                        "content_type": serializers.CharField(),
                    },
                    many=True,
                )
            },
        )
    },
    parameters=[
        OpenApiParameter(
            name="session_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Session ID",
        ),
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes(SESSION_PERMISSION_CLASSES)
@parser_classes([MultiPartParser])
def chat_upload_file(request, session_id):
    session = get_experiment_session_cached(session_id)
    if not session:
        return NotFound()

    if session.is_complete:
        return Response({"error": "Session has ended"}, status=status.HTTP_400_BAD_REQUEST)
    files = request.FILES.getlist("files")
    if not files:
        return Response({"error": "No files provided"}, status=status.HTTP_400_BAD_REQUEST)

    for file in files:
        is_valid, error_msg = validate_file_upload(file)
        if not is_valid:
            return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)

    total_size_mb = sum(f.size for f in files) / (1024 * 1024)
    if total_size_mb > MAX_TOTAL_SIZE_MB:
        return Response(
            {"error": f"Total file size exceeds maximum of {MAX_TOTAL_SIZE_MB}MB"}, status=status.HTTP_400_BAD_REQUEST
        )
    expiry_date = timezone.now() + timezone.timedelta(hours=24)
    uploaded_files = []

    participant_remote_id = request.POST.get("participant_remote_id", "")
    participant_name = request.POST.get("participant_name", "")
    uploaded_by = session.participant.identifier if session.participant else participant_remote_id

    if not uploaded_by and request.user.is_authenticated:
        uploaded_by = request.user.email

    # Default to the remote_id if we still don't have an identifier
    if not uploaded_by:
        uploaded_by = participant_remote_id or "unknown"

    for file in files:
        file_obj = File.objects.create(
            name=file.name,
            file=file,
            team=session.team,
            content_size=file.size,
            content_type=File.get_content_type(file),
            expiry_date=expiry_date,
            purpose="assistant",
            metadata={
                "session_id": str(session_id),
                "uploaded_by": uploaded_by,
                "participant_name": participant_name,
                "participant_remote_id": participant_remote_id,
            },
        )
        uploaded_files.append(
            {
                "id": file_obj.id,
                "name": file_obj.name,
                "size": file_obj.content_size,
                "content_type": file_obj.content_type,
            }
        )

    return Response({"files": uploaded_files}, status=status.HTTP_201_CREATED)


@extend_schema(
    operation_id="chat_start_session",
    summary="Start a new chat session for a widget",
    tags=["Chat"],
    request=ChatStartSessionRequest,
    responses={201: ChatStartSessionResponse},
    # auth=["{}"],
    examples=[
        OpenApiExample(
            name="StartSessionWorkingVersion",
            summary="Start session with working (unreleased) version",
            value={
                "chatbot_id": "123e4567-e89b-12d3-a456-426614174000",
                "session_data": {"source": "widget", "page_url": "https://example.com"},
                "participant_remote_id": "abc",
                "participant_name": "participant_name",
            },
            request_only=True,
        ),
        OpenApiExample(
            name="StartSessionSpecificVersion",
            summary="Start session with specific published version (requires auth)",
            value={
                "chatbot_id": "123e4567-e89b-12d3-a456-426614174000",
                "version_number": 2,
                "participant_remote_id": "abc",
                "participant_name": "participant_name",
            },
            request_only=True,
        ),
        OpenApiExample(
            name="StartSessionWorkingVersionResponse",
            summary="Session started with published version",
            value={
                "session_id": "123e4567-e89b-12d3-a456-426614174000",
                "chatbot": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "name": "Example Bot",
                    "version_number": 0,
                    "versions": [],
                    "url": "https://example.com/api/experiments/123e4567-e89b-12d3-a456-426614174000/",
                },
                "participant": {"identifier": "abc", "remote_id": "abc"},
            },
            response_only=True,
        ),
        OpenApiExample(
            name="StartSessionSpecificVersionResponse",
            summary="Session started with specific published version",
            value={
                "session_id": "123e4567-e89b-12d3-a456-426614174000",
                "chatbot": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "name": "Example Bot",
                    "version_number": 2,
                    "versions": [],
                    "url": "https://example.com/api/experiments/123e4567-e89b-12d3-a456-426614174000/",
                },
                "participant": {"identifier": "abc", "remote_id": "abc"},
            },
            response_only=True,
        ),
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes([WidgetDomainPermission])
def chat_start_session(request):
    """Start a new chat session - supports both authenticated users and embedded widgets"""
    serializer = ChatStartSessionRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    experiment_id = data["chatbot_id"]
    version_number = data.get("version_number")
    session_data = data.get("session_data", {})
    remote_id = data.get("participant_remote_id", "")
    name = data.get("participant_name")

    # Security check: Only authenticated users can specify version numbers
    if version_number is not None and not request.user.is_authenticated:
        return Response(
            {"error": "Version number requires authentication"},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Always look up the working version by public_id
    experiment = get_object_or_404(Experiment, public_id=experiment_id, working_version_id__isnull=True)

    experiment_version = None
    if version_number is not None:
        # Verify the authenticated user belongs to the experiment's team
        if not experiment.team.members.filter(id=request.user.id).exists():
            return Response(
                {"error": "You do not have access to this chatbot"},
                status=status.HTTP_403_FORBIDDEN,
            )

        experiment_version = get_object_or_404(
            Experiment, working_version_id=experiment.id, version_number=version_number
        )

    team = experiment.team

    # Check if authenticated via DRF EmbeddedWidgetAuthentication
    if isinstance(request.auth, ExperimentChannel):
        experiment_channel = request.auth
    else:
        # legacy flow
        experiment_channel = ExperimentChannel.objects.get_team_api_channel(team)

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
        participant, created = Participant.objects.get_or_create(
            identifier=participant_id,
            team=team,
            platform=experiment_channel.platform,
            defaults={"user": user, "remote_id": remote_id},
        )
    else:
        participant = Participant.create_anonymous(team, experiment_channel.platform, remote_id)

    if name:
        if participant.name != name:
            participant.name = name
            participant.save(update_fields=["name"])
        participant_data, _ = ParticipantData.objects.get_or_create(
            participant=participant, experiment=experiment, team=team, defaults={"data": {}}
        )
        if participant_data.data.get("name") != name:
            participant_data.data["name"] = name
            participant_data.save(update_fields=["data"])

    metadata = {Chat.MetadataKeys.EMBED_SOURCE: request.headers.get("referer", None)}

    session = ApiChannel.start_new_session(
        working_experiment=experiment,
        experiment_channel=experiment_channel,
        participant_identifier=participant.identifier,
        participant_user=user,
        metadata=metadata,
        version=version_number if version_number is not None else Experiment.DEFAULT_VERSION_NUMBER,
    )

    if user is not None and session_data:
        session.state = session_data
        session.save(update_fields=["state"])

    # Prepare response data
    response_data = {
        "session_id": session.external_id,
        "chatbot": experiment_version or experiment,
        "participant": participant,
    }

    serialized_response = ChatStartSessionResponse(response_data, context={"request": request})
    return Response(serialized_response.data, status=status.HTTP_201_CREATED)


class ChatSendMessageRequestWithAttachments(ChatSendMessageRequest):
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, help_text="List of file IDs from prior upload"
    )
    version_number = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Optional version number of the chatbot to use. Requires authentication.",
    )


@extend_schema(
    operation_id="chat_send_message",
    summary="Send a message to a chat session",
    tags=["Chat"],
    request=ChatSendMessageRequestWithAttachments,
    responses={202: ChatSendMessageResponse},
    parameters=[
        OpenApiParameter(
            name="session_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Session ID",
        ),
    ],
    examples=[
        OpenApiExample(
            name="SendMessage",
            summary="Send a message to the bot",
            value={"message": "Hello, how can you help me?"},
        ),
        OpenApiExample(
            name="SendMessageWithAttachments",
            summary="Send a message with file attachments",
            value={"message": "Please review these documents", "attachment_ids": [123, 124]},
        ),
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes(SESSION_PERMISSION_CLASSES)
def chat_send_message(request, session_id):
    """
    Send a message to a chat session
    """
    serializer = ChatSendMessageRequestWithAttachments(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    message_text = data["message"]
    attachment_ids = data.get("attachment_ids", [])
    version_number = data.get("version_number")
    context = data.get("context", {})

    session = get_experiment_session_cached(session_id)
    if not session:
        return NotFound()

    # Verify session is active
    if session.is_complete:
        return Response({"error": "Session has ended"}, status=status.HTTP_400_BAD_REQUEST)

    if version_number is not None:
        if not request.user.is_authenticated:
            return Response(
                {"error": "Version number requires authentication"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not session.experiment.team.members.filter(id=request.user.id).exists():
            return Response(
                {"error": "You do not have access to this chatbot"},
                status=status.HTTP_403_FORBIDDEN,
            )
        experiment_version = get_object_or_404(
            Experiment, working_version_id=session.experiment.id, version_number=version_number
        )
    else:
        experiment_version = session.experiment_version

    attachment_data = []
    if attachment_ids:
        files = File.objects.filter(id__in=attachment_ids, team=session.team)

        if files.count() != len(attachment_ids):
            return Response({"error": "One or more file IDs are invalid"}, status=status.HTTP_400_BAD_REQUEST)
        files.update(expiry_date=None)
        chat_attachment, created = ChatAttachment.objects.get_or_create(
            chat=session.chat,
            tool_type="ocs_attachments",
        )
        chat_attachment.files.add(*files)
        for file_obj in files:
            attachment = Attachment.from_file(file_obj, type="ocs_attachments", session_id=session.id)
            attachment_data.append(attachment.model_dump())

    # Queue the response generation as a background task
    task_id = get_response_for_webchat_task.delay(
        experiment_session_id=session.id,
        experiment_id=experiment_version.id,
        message_text=message_text,
        attachments=attachment_data if attachment_data else None,
        context=context,
    ).task_id

    response_data = ChatSendMessageResponse({"task_id": task_id, "status": "processing"}).data
    return Response(response_data, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    operation_id="chat_poll_task_response",
    summary="Poll for task updates",
    tags=["Chat"],
    responses={
        200: inline_serializer(
            "ChatTaskPoll",
            {
                "message": MessageSerializer(required=False),
                "status": serializers.ChoiceField(required=False, choices=("processing", "complete")),
            },
        ),
        500: inline_serializer(
            "ChatTaskPollError",
            {
                "error": serializers.CharField(required=False),
                "status": "error",
            },
        ),
    },
    parameters=[
        OpenApiParameter(
            name="session_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Session ID",
        ),
        OpenApiParameter(
            name="task_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Check on the status of a specific task",
            required=True,
        ),
    ],
)
@api_view(["GET"])
@authentication_classes(AUTH_CLASSES)
@permission_classes(SESSION_PERMISSION_CLASSES)
def chat_poll_task_response(request, session_id, task_id):
    session = get_experiment_session_cached(session_id)
    if not session:
        return NotFound()

    experiment = session.experiment
    task_details = get_message_task_response(experiment, task_id)
    if not task_details:
        return Response({"status": "processing"}, status=status.HTTP_200_OK)

    if not task_details["complete"]:
        message_text = get_progress_message(session_id, experiment.name, experiment.description, throttle_key=task_id)
        message = None
        if message_text:
            message = MessageSerializer(ChatMessage(content=message_text, message_type=ChatMessageType.AI)).data
        data = {"message": message, "status": "processing"}
        return Response(data, status=status.HTTP_200_OK)

    if error := task_details["error_msg"]:
        data = {"error": error, "status": "error"}
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if message := task_details["message"]:
        data = {
            "message": MessageSerializer(message, context={"request": request}).data,
            "status": "complete",
        }
        return Response(data, status=status.HTTP_200_OK)

    return Response({"error": "Unknown error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    operation_id="chat_poll_response",
    summary="Poll for new messages in a chat session. Do not poll more than once every 30 seconds",
    tags=["Chat"],
    responses={200: ChatPollResponse},
    parameters=[
        OpenApiParameter(
            name="session_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Session ID",
        ),
        OpenApiParameter(
            name="since",
            type=OpenApiTypes.DATETIME,
            location=OpenApiParameter.QUERY,
            description="Only return messages after this timestamp",
            required=False,
        ),
        OpenApiParameter(
            name="limit",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Maximum number of messages to return (default: 50)",
            required=False,
        ),
    ],
)
@api_view(["GET"])
@authentication_classes(AUTH_CLASSES)
@permission_classes(SESSION_PERMISSION_CLASSES)
def chat_poll_response(request, session_id):
    """
    Poll for new messages in a chat session
    """
    session = get_experiment_session_cached(session_id)
    if not session:
        return NotFound()

    since_param = request.query_params.get("since")
    limit = int(request.query_params.get("limit", 50))
    messages_query = session.chat.messages.order_by("created_at")

    if since_param:
        try:
            since_datetime = timezone.datetime.fromisoformat(since_param.replace("Z", "+00:00"))
            messages_query = messages_query.filter(created_at__gt=since_datetime)
        except ValueError:
            return Response(
                {"error": "Invalid 'since' parameter format. Use ISO format."}, status=status.HTTP_400_BAD_REQUEST
            )

    # Get messages with limit
    messages = list(messages_query[: limit + 1])  # Get one extra to check if there are more
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    session_status = "ended" if session.is_complete else "active"
    response_data = {"messages": messages, "has_more": has_more, "session_status": session_status}
    return Response(ChatPollResponse(response_data).data, status=status.HTTP_200_OK)


def get_progress_message(session_id, chatbot_name, chatbot_description, throttle_key=None) -> str | None:
    """Get the next progress message. This will generate new messages if there are no more messages.

    If throttle_key is provided, a new message is only returned once every 5 seconds.
    Within the 5-second window the same message is returned.
    """
    last_key = f"progress_last:{throttle_key}" if throttle_key else None
    if last_key:
        last = cache.get(last_key)
        if last:
            return last

    key = f"progress_messages:{session_id}"
    messages = cache.get(key)
    if not messages:
        messages = get_progress_messages(chatbot_name, chatbot_description)

    if not messages:
        return None

    message, *remainder = messages
    if remainder:
        cache.set(key, remainder, 24 * 3600)
    else:
        cache.delete(key)

    if last_key:
        cache.set(last_key, message, 5)

    return message


def get_progress_messages(chatbot_name, chatbot_description) -> list[str]:
    try:
        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name=chatbot_name, chatbot_description=chatbot_description)
        )
        return agent.run().messages
    except Exception:
        logger.exception("Failed to generate progress messages for chatbot '%s'", chatbot_name)
        return []
