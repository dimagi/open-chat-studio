import json
import logging
import uuid

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask
from telebot import types
from twilio.request_validator import RequestValidator

from apps.channels.datamodels import ApiMessage, TelegramMessage, TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import ApiChannel, FacebookMessengerChannel, TelegramChannel, WhatsappChannel
from apps.experiments.models import ExperimentSession
from apps.service_providers.models import MessagingProviderType
from apps.utils.taskbadger import update_taskbadger_data

log = logging.getLogger(__name__)


@shared_task(bind=True, base=TaskbadgerTask)
def handle_telegram_message(self, message_data: str, channel_external_id: uuid):
    experiment_channel = (
        ExperimentChannel.objects.filter(external_id=channel_external_id).select_related("experiment").first()
    )
    if not experiment_channel:
        return

    update = types.Update.de_json(message_data)
    if update.my_chat_member:
        # This is a chat member update that we don't care about.
        # See https://core.telegram.org/bots/api-changelog#march-9-2021
        return

    message = TelegramMessage.parse(update)
    message_handler = TelegramChannel(experiment_channel=experiment_channel)
    update_taskbadger_data(self, message_handler, message)
    message_handler.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask)
def handle_twilio_message(self, message_data: str, request_uri: str, signature: str):
    raw_data = json.loads(message_data)
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

    experiment_channel = ExperimentChannel.objects.filter(
        extra_data__contains={channel_id_key: message.to}, messaging_provider__type=MessagingProviderType.twilio
    ).first()
    if not experiment_channel:
        return

    validate_twillio_request(experiment_channel, raw_data, request_uri, signature)

    message_handler = ChannelClass(experiment_channel=experiment_channel)
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
def handle_turn_message(self, experiment_id: uuid, message_data: dict):
    message = TurnWhatsappMessage.parse(message_data)
    experiment_channel = ExperimentChannel.objects.filter(
        experiment__public_id=experiment_id,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider__type=MessagingProviderType.turnio,
    ).first()
    if not experiment_channel:
        return
    channel = WhatsappChannel(experiment_channel=experiment_channel)
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)


def handle_api_message(
    experiment_channel: ExperimentChannel, message_data: dict, session: ExperimentSession
) -> tuple[str, str]:
    """Synchronously handles the message coming from the API"""
    message = ApiMessage(participant_id=message_data["participant_id"], message=message_data["message"])

    if not session:
        session = ApiChannel.start_new_session(
            experiment=experiment_channel.experiment,
            experiment_channel=experiment_channel,
            participant_identifier=message_data["participant_id"],
        )

    channel = ApiChannel(experiment_channel=experiment_channel, experiment_session=session)
    return channel.new_user_message(message), str(session.external_id)
