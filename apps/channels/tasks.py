import json
import uuid

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask
from telebot import types

from apps.channels.datamodels import FacebookMessage, TelegramMessage, TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import FacebookMessengerChannel, TelegramChannel, WhatsappChannel
from apps.service_providers.models import MessagingProviderType
from apps.utils.taskbadger import update_taskbadger_data


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
def handle_twilio_message(self, message_data: str):
    message = TwilioMessage.parse(json.loads(message_data))
    experiment_channel = ExperimentChannel.objects.filter(
        extra_data__contains={"number": message.to_number}, messaging_provider__type=MessagingProviderType.twilio
    ).first()
    if not experiment_channel:
        return
    message_handler = WhatsappChannel(experiment_channel=experiment_channel)
    update_taskbadger_data(self, message_handler, message)
    message_handler.new_user_message(message)


@shared_task(bind=True, base=TaskbadgerTask)
def handle_facebook_message(self, team_slug: str, message_data: str):
    data = json.loads(message_data)
    message = data["entry"][0]["messaging"][0]
    message = FacebookMessage.parse(message)
    experiment_channel = ExperimentChannel.objects.filter_extras(
        platform=ChannelPlatform.FACEBOOK, team_slug=team_slug, key="page_id", value=message.page_id
    ).first()
    if not experiment_channel:
        return
    message_handler = FacebookMessengerChannel(experiment_channel=experiment_channel)
    update_taskbadger_data(self, message_handler, message)
    message_handler.new_user_message(message)


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
