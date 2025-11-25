import textwrap
import time
import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from apps.api.serializers import ExperimentSessionCreateSerializer, MessageSerializer
from apps.channels.tasks import handle_api_message
from apps.oauth.permissions import TokenHasRequiredOAuthScope

create_chat_completion_request = inline_serializer(
    "CreateChatCompletionRequest", {"messages": MessageSerializer(many=True)}
)

create_chat_completion_response = inline_serializer(
    "CreateChatCompletionResponse",
    {
        "id": serializers.CharField(),
        "choices": inline_serializer(
            "ChatCompletionResponseChoices",
            {
                "finish_reason": serializers.CharField(),
                "index": serializers.IntegerField(),
                "message": inline_serializer(
                    "ChatCompletionResponseMessage",
                    {
                        "role": serializers.ChoiceField(choices=["assistant"]),
                        "content": serializers.CharField(),
                    },
                ),
            },
            many=True,
        ),
        "created": serializers.IntegerField(),
        "model": serializers.CharField(),
        "object": serializers.ChoiceField(choices=["chat.completion"]),
    },
)


def chat_completions_schema(versioned: bool):
    operation_id = "openai_chat_completions"
    summary = "Chat Completions API for Experiments"
    base_url = "https://chatbots.dimagi.com/api/openai/{experiment_id}"

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
        summary = "Versioned Chat Completions API for Experiments"
        base_url = "https://chatbots.dimagi.com/api/openai/{experiment_id}/v{version}"
        parameters.append(
            OpenApiParameter(
                name="version",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Version of experiment",
            )
        )

    description = textwrap.dedent(
        f"""
        Use OpenAI's client to send messages to the experiment and get responses. This will
        create a new session in the experiment with all the provided messages
        and return the response from the experiment.

        The last message must be a 'user' message.

        Example (Python):

        ```python
        experiment_id = "your experiment ID"

        client = OpenAI(
            api_key="your API key",
            base_url=f"{base_url}",
        )

        completion = client.chat.completions.create(
            model="anything",
            messages=[
                {{"role": "assistant", "content": "How can I help you today?"}},
                {{"role": "user", "content": "I need help with something."}},
            ],
        )

        reply = completion.choices[0].message
        ```
        """
    )

    return extend_schema(
        operation_id=operation_id,
        summary=summary,
        description=description,
        tags=["OpenAI"],
        request=create_chat_completion_request,
        responses={200: create_chat_completion_response},
        parameters=parameters,
    )


@chat_completions_schema(versioned=False)
@api_view(["POST"])
@permission_classes([TokenHasRequiredOAuthScope("chatbots:interact")])
def chat_completions(request, experiment_id: uuid.UUID):
    return _chat_completions(request, experiment_id)


@chat_completions_schema(versioned=True)
@api_view(["POST"])
@permission_classes([TokenHasRequiredOAuthScope("chatbots:interact")])
def chat_completions_version(request, experiment_id: uuid.UUID, version=None):
    return _chat_completions(request, experiment_id, version)


def _chat_completions(request, experiment_id: uuid.UUID, version=None):
    try:
        messages = [_convert_message(message) for message in request.data.get("messages", [])]
    except APIException as e:
        return _make_error_response(400, str(e))
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
    experiment_version = session.experiment.get_version(version) if version is not None else session.experiment_version
    response_message = handle_api_message(
        request.user,
        experiment_version,
        session.experiment_channel,
        last_message.get("content"),
        session.participant.identifier,
        session,
    )
    completion = {
        "id": session.external_id,
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {"role": "assistant", "content": response_message.content},
            }
        ],
        "created": int(time.time()),
        "model": session.experiment.get_llm_provider_model_name(),
        "object": "chat.completion",
    }
    return Response(data=completion)


def _make_error_response(status_code, message):
    data = {"error": {"message": message, "type": "error", "param": None, "code": None}}
    return Response(data=data, status=status_code)


def _convert_message(message):
    content = message.get("content")
    if isinstance(content, list):
        text_messages = [part for part in content if part.get("type") == "text"]
        if len(text_messages) != len(content):
            raise APIException("Only text messages are supported")
        content = " ".join(part["text"] for part in text_messages)
        message["content"] = content
    return message
