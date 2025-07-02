from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.api.permissions import ApiKeyAuthentication, BearerTokenAuthentication
from apps.api.serializers import (
    ChatPollResponse,
    ChatSendMessageRequest,
    ChatSendMessageResponse,
    ChatStartSessionRequest,
    ChatStartSessionResponse,
    MessageSerializer,
)
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import ApiChannel, WebChannel
from apps.chat.models import Chat
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.experiments.task_utils import get_message_task_response
from apps.experiments.tasks import get_response_for_webchat_task

AUTH_CLASSES = [ApiKeyAuthentication, BearerTokenAuthentication]


def check_experiment_access(request, experiment, participant_id):
    """
    Check if the request has access to the experiment based on public API settings.

    Returns:
        Response object if access denied, None if access allowed
    """
    if request.team and experiment.team != request.team:
        # Authenticated requests must be constrained to their team
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    if not experiment.is_public and not experiment.is_participant_allowed(participant_id):
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    return None


def check_session_access(request, session):
    """
    Check if the request has access to the session based on experiment's public API settings.

    Returns:
        Response object if access denied, None if access allowed
    """
    return check_experiment_access(request, session.experiment, session.participant.identifier)


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
                "experiment_id": "123e4567-e89b-12d3-a456-426614174000",
                "session_data": {"source": "widget", "page_url": "https://example.com"},
            },
        ),
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes([])
def chat_start_session(request):
    """
    Start a new chat session for a widget
    """
    serializer = ChatStartSessionRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    experiment_id = data["chatbot_id"]
    participant_id = data.get("participant_id")
    session_data = data.get("session_data", {})

    # First, check if this is a public experiment
    experiment = get_object_or_404(Experiment, public_id=experiment_id)
    if not experiment.is_working_version:
        return Response({"error": "Chatbot ID must be for a working version"}, status=status.HTTP_400_BAD_REQUEST)

    access_response = check_experiment_access(request, experiment, participant_id)
    if access_response:
        return access_response

    if request.user.is_authenticated:
        team = request.team
        user = request.user
    else:
        team = experiment.team
        user = None

    # Create or get participant
    if user is not None:
        participant_id = participant_id or user.email
        if participant_id != user.email:
            # TODO: re-evaluate this, it doesn't seem correct in this instance
            return Response(
                {"error": "Participant ID must match your email address"}, status=status.HTTP_400_BAD_REQUEST
            )

        participant, created = Participant.objects.get_or_create(
            identifier=participant_id, team=team, platform=ChannelPlatform.API, defaults={"user": user}
        )
    else:
        participant = Participant.create_anonymous(team, ChannelPlatform.API)

    api_channel = ExperimentChannel.objects.get_team_api_channel(team)

    session = ApiChannel.start_new_session(
        working_experiment=experiment,
        experiment_channel=api_channel,
        participant_identifier=participant.identifier,
        # timezone
        # session_external_id
        metadata={Chat.MetadataKeys.EMBED_SOURCE: request.headers.get("referer", None)},
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


@extend_schema(
    operation_id="chat_send_message",
    summary="Send a message to a chat session",
    tags=["Chat"],
    request=ChatSendMessageRequest,
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
    ],
)
@api_view(["POST"])
@authentication_classes(AUTH_CLASSES)
@permission_classes([])
def chat_send_message(request, session_id):
    """
    Send a message to a chat session
    """
    serializer = ChatSendMessageRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data
    message_text = data["message"]

    session = get_object_or_404(ExperimentSession, external_id=session_id)

    access_response = check_session_access(request, session)
    if access_response:
        return access_response

    # Verify session is active
    if session.is_complete:
        return Response({"error": "Session has ended"}, status=status.HTTP_400_BAD_REQUEST)

    # TODO Handle attachments if provided

    # Queue the response generation as a background task
    experiment_version = session.experiment_version
    task_id = get_response_for_webchat_task.delay(
        experiment_session_id=session.id,
        experiment_id=experiment_version.id,
        message_text=message_text,
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

    access_response = check_session_access(request, session)
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

    access_response = check_session_access(request, session)
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
