import time

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.api.permissions import HasUserAPIKey
from apps.api.serializers import ExperimentSessionCreateSerializer
from apps.channels.tasks import handle_api_message


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
def chat_completions(request, experiment_id: str):
    messages = request.data.get("messages", [])
    try:
        last_message = messages.pop()
    except IndexError:
        # TODO: openai error responses
        return Response(data={})

    if last_message.get("role") != "user":
        # TODO: openai error responses
        return Response(data={})

    converted_data = {
        "experiment": experiment_id,
        "messages": messages,
    }
    serializer = ExperimentSessionCreateSerializer(data=converted_data, context={"request": request})
    serializer.is_valid(raise_exception=True)  # TODO: openai error responses
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
