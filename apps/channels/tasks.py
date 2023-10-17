import json
import uuid

from celery.app import shared_task
from telebot import types

from apps.channels.datamodels import WhatsappMessage
from apps.channels.models import ExperimentChannel
from apps.chat.channels import TelegramChannel, WhatsappChannel


@shared_task
def handle_telegram_message(message_data: str, channel_external_id: uuid):
    experiment_channel = ExperimentChannel.objects.filter(external_id=channel_external_id).first()
    if not experiment_channel:
        return
    update = types.Update.de_json(message_data)
    message_handler = TelegramChannel(experiment_channel=experiment_channel)
    message_handler.new_user_message(update.message)


@shared_task
def handle_whatsapp_message(message_data: str):
    message = WhatsappMessage.parse_obj(json.loads(message_data))
    experiment_channel = ExperimentChannel.objects.filter(extra_data__contains={"number": message.to_number}).first()
    if not experiment_channel:
        return
    message_handler = WhatsappChannel(experiment_channel=experiment_channel)
    message_handler.new_user_message(message)
