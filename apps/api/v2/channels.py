from django.db import transaction
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.views import APIView

from apps.api.serializers import TriggerBotMessageRequest
from apps.api.v2.serializers import TriggerBotMessageResponse
from apps.api.views.channels import handle_trigger_bot_message


class TriggerBotMessageView(APIView):
    required_scopes = ("chatbots:interact",)

    @extend_schema(
        operation_id="trigger_bot_message",
        summary="Trigger the bot to send a message to the user, or deliver a message directly",
        tags=["Channels"],
        request=TriggerBotMessageRequest(),
        responses={
            200: TriggerBotMessageResponse,
            400: {"description": "Bad Request"},
            404: {"description": "Not Found"},
        },
        examples=[
            OpenApiExample(
                name="GenerateBotMessageAndSend",
                summary="Generates a bot message and sends it to the user (auto-creates participant if needed).",
                value={
                    "identifier": "+15556793",
                    "experiment": "exp1",
                    "platform": "whatsapp",
                    "prompt_text": "Tell the user to do something",
                    "session_data": {"key": "value"},
                    "participant_data": {"key": "value"},
                },
                status_codes=[200],
            ),
            OpenApiExample(
                name="SendMessageDirectly",
                summary="Send a pre-written message directly to the participant, bypassing the bot/LLM.",
                value={
                    "identifier": "+15556793",
                    "experiment": "exp1",
                    "platform": "whatsapp",
                    "message_text": "Your appointment is confirmed for tomorrow at 10am.",
                    "session_data": {"key": "value"},
                    "participant_data": {"key": "value"},
                },
                status_codes=[200],
            ),
            OpenApiExample(
                name="ExperimentChannelNotFound",
                summary="Experiment cannot send messages on the specified channel",
                value={"detail": "Experiment cannot send messages on the connect_messaging channel"},
                status_codes=[404],
            ),
            OpenApiExample(
                name="ConsentNotGiven",
                summary="User has not given consent",
                value={"detail": "User has not given consent"},
                status_codes=[400],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        """
        Trigger the bot to send a message to the user, or deliver a message directly.

        Provide either ``prompt_text`` (routes through the LLM/bot pipeline) or ``message_text``
        (sends the exact text to the participant without any LLM processing). Exactly one is required.

        The response ``channel`` is an object of ``{platform, data}``; for CommCare Connect, ``data``
        carries the ``external_channel_id`` of the session's Connect channel.
        """
        return handle_trigger_bot_message(request, TriggerBotMessageResponse)
