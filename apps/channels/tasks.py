import json
import logging
import uuid

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask
from telebot import types
from twilio.request_validator import RequestValidator

from apps.channels.clients.connect_client import CommCareConnectClient, Message
from apps.channels.datamodels import BaseMessage, SureAdhereMessage, TelegramMessage, TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import (
    ApiChannel,
    CommCareConnectChannel,
    EvaluationChannel,
    FacebookMessengerChannel,
    SureAdhereChannel,
    TelegramChannel,
    WhatsappChannel,
)
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.service_providers.models import MessagingProviderType
from apps.utils.taskbadger import update_taskbadger_data

log = logging.getLogger("ocs.channels")


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_telegram_message(self, message_data: str, channel_external_id: uuid):
    experiment_channel = (
        ExperimentChannel.objects.filter(external_id=channel_external_id).select_related("experiment", "team").first()
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for external_id: {channel_external_id}")
        return

    update = types.Update.de_json(message_data)
    if update.my_chat_member:
        # This is a chat member update that we don't care about.
        # See https://core.telegram.org/bots/api-changelog#march-9-2021
        return

    message = TelegramMessage.parse(update)
    message_handler = TelegramChannel(experiment_channel.experiment.default_version, experiment_channel)
    update_taskbadger_data(self, message_handler, message)

    message_handler.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_twilio_message(self, message_data: str, request_uri: str, signature: str):
    raw_data = json.loads(message_data)
    if "Body" not in raw_data:
        log.info(f"Received a Twilio status update, not a message: {raw_data}")
        return
    message = TwilioMessage.parse(raw_data)

    channel_id_key = ""
    ChannelClass = None
    match message.platform:
        case ChannelPlatform.WHATSAPP:
            channel_id_key = "number"
            ChannelClass = WhatsappChannel
        case ChannelPlatform.FACEBOOK:
            channel_id_key = "page_id"
            ChannelClass = FacebookMessengerChannel

    experiment_channel = (
        ExperimentChannel.objects.filter(
            extra_data__contains={channel_id_key: message.to}, messaging_provider__type=MessagingProviderType.twilio
        )
        .select_related("experiment", "team")
        .first()
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for {channel_id_key}: {message.to}")
        return

    validate_twillio_request(experiment_channel, raw_data, request_uri, signature)

    message_handler = ChannelClass(experiment_channel.experiment.default_version, experiment_channel=experiment_channel)
    update_taskbadger_data(self, message_handler, message)

    message_handler.new_user_message(message)


def validate_twillio_request(experiment_channel, raw_data, request_uri, signature):
    """For now this just logs an error if the signature validation fails.
    In the future we will want to raise an error.

    See https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    try:
        auth_token = experiment_channel.messaging_provider.get_messaging_service().auth_token
        request_valid = RequestValidator(auth_token).validate(request_uri, raw_data, signature)
    except Exception:
        log.exception("Twilio signature validation failed")
    else:
        if not request_valid:
            log.error("Twilio signature validation failed")


@shared_task(bind=True, base=TaskbadgerTask)
def handle_sureadhere_message(self, sureadhere_tenant_id: str, message_data: dict):
    message = SureAdhereMessage.parse(message_data)
    experiment_channel = (
        ExperimentChannel.objects.filter(
            extra_data__sureadhere_tenant_id=sureadhere_tenant_id,
            platform=ChannelPlatform.SUREADHERE,
            messaging_provider__type=MessagingProviderType.sureadhere,
        )
        .select_related("experiment", "team")
        .first()
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for SureAdhere tenant ID: {sureadhere_tenant_id}")
        return
    channel = SureAdhereChannel(experiment_channel.experiment.default_version, experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_turn_message(self, experiment_id: uuid, message_data: dict):
    message = TurnWhatsappMessage.parse(message_data)
    experiment_channel = (
        ExperimentChannel.objects.filter(
            experiment__public_id=experiment_id,
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider__type=MessagingProviderType.turnio,
        )
        .select_related("experiment", "team")
        .first()
    )
    if not experiment_channel:
        log.info(f"No experiment channel found for experiment_id: {experiment_id}")
        return
    channel = WhatsappChannel(experiment_channel.experiment.default_version, experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


def handle_api_message(
    user, experiment_version, experiment_channel, message_text: str, participant_id: str, session=None
) -> ChatMessage:
    """Synchronously handles the message coming from the API"""
    message = BaseMessage(participant_id=participant_id, message_text=message_text)
    channel = ApiChannel(
        experiment_version,
        experiment_channel,
        experiment_session=session,
        user=user,
    )
    return channel.new_user_message(message)


def handle_evaluation_message(
    experiment_version, experiment_channel, message_text: str, session: ExperimentSession, participant_data: dict
) -> ChatMessage:
    """Synchronously handles the message coming from evaluations"""
    message = BaseMessage(participant_id=session.participant.identifier, message_text=message_text)
    channel = EvaluationChannel(
        experiment_version, experiment_channel, experiment_session=session, participant_data=participant_data
    )
    return channel.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask, ignore_result=True)
def handle_commcare_connect_message(
    self, experiment_channel_id: int, participant_data_id: int, messages: list[Message]
):
    participant_data = ParticipantData.objects.prefetch_related("participant").get(id=participant_data_id)
    experiment_channel = ExperimentChannel.objects.prefetch_related("experiment").get(id=experiment_channel_id)

    # Ensure the messages are in the correct order according to timestamp
    messages.sort(key=lambda x: x["timestamp"])

    connect_client = CommCareConnectClient()
    decrypted_messages = connect_client.decrypt_messages(participant_data.get_encryption_key_bytes(), messages=messages)

    # If the user sent multiple messages, we should append it together instead of the bot replying to each one
    user_message = "\n\n".join(decrypted_messages)

    message = BaseMessage(participant_id=participant_data.participant.identifier, message_text=user_message)
    channel = CommCareConnectChannel(
        experiment=experiment_channel.experiment.default_version, experiment_channel=experiment_channel
    )

    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)
