import pathlib
import uuid

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.api.serializers import (
    ChatPollResponse,
    ChatSendMessageRequest,
    ChatSendMessageResponse,
    ChatStartSessionRequest,
    ChatStartSessionResponse,
    MessageSerializer,
)
from apps.channels.datamodels import Attachment
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import (
    extract_domain_from_headers,
    validate_embed_key_for_experiment,
)
from apps.chat.channels import ApiChannel, WebChannel
from apps.chat.models import Chat, ChatAttachment
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.experiments.task_utils import get_message_task_response
from apps.experiments.tasks import get_response_for_webchat_task
from apps.files.models import File

AUTH_CLASSES = [SessionAuthentication]

MAX_FILE_SIZE_MB = settings.MAX_FILE_SIZE_MB
MAX_TOTAL_SIZE_MB = 50
SUPPORTED_FILE_EXTENSIONS = settings.SUPPORTED_FILE_TYPES["collections"]


def check_experiment_access(experiment, participant_id):
    """
    Check if the request has access to the experiment based on public API settings.

    Returns:
        Response object if access denied, None if access allowed
    """
    if experiment.is_public:
        return None

    if not participant_id:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    if not experiment.is_participant_allowed(participant_id):
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    return None


def handle_embedded_widget_auth(request, experiment_id=None, session=None):
    embed_key = request.headers.get("X-Embed-Key")
    if not embed_key:
        return False, None, None

    # Extract origin domain from headers
    origin_domain = extract_domain_from_headers(request)
    if not origin_domain:
        raise PermissionDenied("Origin or Referer header required for embedded widgets")

    if experiment_id:
        target_experiment_id = experiment_id
    elif session:
        target_experiment_id = session.experiment.public_id
    else:
        raise ValueError("Either experiment_id or session must be provided")

    experiment_channel = validate_embed_key_for_experiment(
        token=embed_key, origin_domain=origin_domain, experiment_id=target_experiment_id
    )

    if not experiment_channel:
        raise PermissionDenied("Invalid embed key or domain not allowed")

    # Generate id for anon widget users
    participant_remote_id = None
    if hasattr(request, "data") and request.data:
        participant_remote_id = request.data.get("participant_remote_id")

    if not participant_remote_id:
        participant_remote_id = f"embed_{uuid.uuid4()}"

    return True, experiment_channel, participant_remote_id


def check_session_access(session, request=None):
    """
    Check if the request has access to the session.
    Now handles both authenticated users and embedded widgets.

    Returns:
        Response object if access denied, None if access allowed
    """
    if session.experiment_channel.platform == ChannelPlatform.EMBEDDED_WIDGET:
        if not request:
            return Response(
                {"error": "Request context required for embedded widgets"}, status=status.HTTP_403_FORBIDDEN
            )
        try:
            handle_embedded_widget_auth(request, session=session)
            return None  # Access allowed
        except PermissionDenied as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception:
            return Response({"error": "Embedded widget authentication failed"}, status=status.HTTP_403_FORBIDDEN)
    return check_experiment_access(session.experiment, session.participant.identifier)


def validate_file_upload(file):
    """
    Validate a file upload for size and type restrictions.
    Returns: tuple: (is_valid, error_message)
    """
    file_size_mb = file.size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        return False, f"File '{file.name}' exceeds maximum size of {MAX_FILE_SIZE_MB}MB"
    file_ext = pathlib.Path(file.name).suffix.lower()
    if file_ext not in SUPPORTED_FILE_EXTENSIONS:
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
@permission_classes([])
@parser_classes([MultiPartParser])
def chat_upload_file(request, session_id):
    session = get_object_or_404(ExperimentSession, external_id=session_id)
    access_response = check_session_access(session, request)
    if access_response:
        return access_response

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
            name="StartChatSession",
            summary="Start a new chat session for an experiment",
            value={
                "chatbot_id": "123e4567-e89b-12d3-a456-426614174000",
                "session_data": {"source": "widget", "page_url": "https://example.com"},
                "participant_remote_id": "abc",
                "participant_name": "participant_name",
            },
        ),
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes([])
def chat_start_session(request):
    """Start a new chat session - supports both authenticated users and embedded widgets"""
    serializer = ChatStartSessionRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    experiment_id = data["chatbot_id"]
    session_data = data.get("session_data", {})
    remote_id = data.get("participant_remote_id", "")
    name = data.get("participant_name")

    try:
        is_embedded, experiment_channel, embed_participant_id = handle_embedded_widget_auth(
            request, experiment_id=experiment_id
        )
    except PermissionDenied as e:
        return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

    # Get experiment
    experiment = get_object_or_404(Experiment, public_id=experiment_id)
    if not experiment.is_working_version:
        return Response(
            {"error": "Chatbot ID must reference the unreleased version of an chatbot"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    team = experiment.team

    if is_embedded:
        user = None
        participant_id = remote_id if remote_id else embed_participant_id
        platform = ChannelPlatform.EMBEDDED_WIDGET
        api_channel = experiment_channel
        # Skip public API access checks for embedded widgets

    else:
        platform = ChannelPlatform.API
        api_channel = ExperimentChannel.objects.get_team_api_channel(team)

        if request.user.is_authenticated:
            user = request.user
            participant_id = user.email
            if remote_id != participant_id:
                return Response(
                    {"error": "Remote ID must match your email address"}, status=status.HTTP_400_BAD_REQUEST
                )
            remote_id = ""
        else:
            user = None
            participant_id = None

        access_response = check_experiment_access(experiment, participant_id)
        if access_response:
            return access_response

    # Create or get participant
    if user is not None:
        participant, created = Participant.objects.get_or_create(
            identifier=participant_id,
            team=team,
            platform=platform,
            defaults={"user": user, "remote_id": remote_id},
        )
    else:
        participant = Participant.create_anonymous(team, platform, remote_id)
        if is_embedded:
            participant.identifier = participant_id
            participant.save(update_fields=["identifier"])

    if remote_id and participant.remote_id != remote_id:
        participant.remote_id = remote_id
        participant.save(update_fields=["remote_id"])

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
    if is_embedded:
        metadata["embedded_widget"] = True
        metadata["origin_domain"] = extract_domain_from_headers(request)

    session = ApiChannel.start_new_session(
        working_experiment=experiment,
        experiment_channel=api_channel,
        participant_identifier=participant.identifier,
        participant_user=user,
        metadata=metadata,
    )
    if user is not None and session_data:
        session.state = session_data
        session.save(update_fields=["state"])

    WebChannel.check_and_process_seed_message(session, experiment)

    # Prepare response data
    response_data = {
        "session_id": session.external_id,
        "chatbot": experiment,
        "participant": participant,
    }
    if session.seed_task_id:
        response_data["seed_message_task_id"] = session.seed_task_id

    serialized_response = ChatStartSessionResponse(response_data, context={"request": request})
    return Response(serialized_response.data, status=status.HTTP_201_CREATED)


class ChatSendMessageRequestWithAttachments(ChatSendMessageRequest):
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, help_text="List of file IDs from prior upload"
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
@permission_classes([])
def chat_send_message(request, session_id):
    """
    Send a message to a chat session
    """
    serializer = ChatSendMessageRequestWithAttachments(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    message_text = data["message"]
    attachment_ids = data.get("attachment_ids", [])

    session = get_object_or_404(ExperimentSession, external_id=session_id)

    access_response = check_session_access(session, request)
    if access_response:
        return access_response

    # Verify session is active
    if session.is_complete:
        return Response({"error": "Session has ended"}, status=status.HTTP_400_BAD_REQUEST)

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
    experiment_version = session.experiment_version
    task_id = get_response_for_webchat_task.delay(
        experiment_session_id=session.id,
        experiment_id=experiment_version.id,
        message_text=message_text,
        attachments=attachment_data if attachment_data else None,
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
@permission_classes([])
def chat_poll_task_response(request, session_id, task_id):
    try:
        session = ExperimentSession.objects.select_related("experiment").get(external_id=session_id)
    except ExperimentSession.DoesNotExist:
        raise Http404() from None

    access_response = check_session_access(session, request)
    if access_response:
        return access_response
    task_details = get_message_task_response(session.experiment, task_id)
    if not task_details["complete"]:
        data = {"message": None, "status": "processing"}
        return Response(data, status=status.HTTP_200_OK)

    if error := task_details["error_msg"]:
        data = {"error": error, "status": "error"}
        return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if message := task_details["message"]:
        data = {
            "message": MessageSerializer(message).data,
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
@permission_classes([])
def chat_poll_response(request, session_id):
    """
    Poll for new messages in a chat session
    """
    session = get_object_or_404(ExperimentSession, external_id=session_id)

    access_response = check_session_access(session, request)
    if access_response:
        return access_response
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
