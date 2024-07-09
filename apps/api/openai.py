import time

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.api.permissions import BearerTokenAuthentication
from apps.api.serializers import ExperimentSessionCreateSerializer, MessageSerializer
from apps.channels.tasks import handle_api_message


@extend_schema(
    operation_id="openai_chat_completions",
    summary="Chat Completions API for Experiments",
    description="""
    Use OpenAI's client to send messages to the experiment and get responses. This will
    create a new session in the experiment with all the provided messages
    and return the response from the experiment.
    
    The last message must be a 'user' message.
    
    Example (Python):
    
        experiment_id = "your experiment ID"
        
        client = OpenAI(
            api_key="your API key",
            base_url=f"https://chatbots.dimagi.com/api/openai/{experiment_id}",
        )
        
        completion = client.chat.completions.create(
            model="anything",
            messages=[
                {"role": "assistant", "content": "How can I help you today?"},
                {"role": "user", "content": "I need help with something."},
            ],
        )
        
        reply = completion.choices[0].message
    """,
    tags=["OpenAI"],
    request=inline_serializer(
        "chat.completion.request",
        {"messages": MessageSerializer(many=True)},
    ),
    responses={
        200: inline_serializer(
            "chat.completion",
            {
                "id": serializers.CharField(),
                "choices": inline_serializer(
                    "chat.choices",
                    {
                        "finish_reason": serializers.CharField(),
                        "index": serializers.IntegerField(),
                        "message": serializers.CharField(),
                    },
                    many=True,
                ),
                "created": serializers.IntegerField(),
                "model": serializers.CharField(),
                "object": "chat.completion",
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
@authentication_classes([BearerTokenAuthentication])
@permission_classes([])  # remove the default
def chat_completions(request, experiment_id: str):
    messages = request.data.get("messages", [])
    try:
        last_message = messages.pop()
    except IndexError:
        return _make_error_response(400, "No messages provided")

    if last_message.get("role") != "user":
        return _make_error_response(400, "Last message must be a user message")

    converted_data = {
        "experiment": experiment_id,
        "messages": messages,
    }
    serializer = ExperimentSessionCreateSerializer(data=converted_data, context={"request": request})
    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError as e:
        return _make_error_response(400, str(e))

    session = serializer.save()
    response_message = handle_api_message(
        request.user, session.experiment, last_message.get("content"), session.participant.identifier, session
    )
    completion = {
        "id": session.external_id,
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": response_message,
            }
        ],
        "created": int(time.time()),
        "model": session.experiment.llm,
        "object": "chat.completion",
    }
    return Response(data=completion)


def _make_error_response(status_code, message):
    data = {"error": {"message": message, "type": "error", "param": None, "code": None}}
    return Response(data=data, status=status_code)
