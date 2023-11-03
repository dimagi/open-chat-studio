import json
import uuid

from celery.app import shared_task
from telebot import types

from apps.channels.datamodels import FacebookMessage, WhatsappMessage
from apps.channels.models import ExperimentChannel
from apps.chat.channels import FacebookMessengerChannel, TelegramChannel, WhatsappChannel


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


@shared_task
def handle_facebook_message(message_data: str):
    data = json.loads(message_data)
    message = data["entry"][0]["messaging"][0]
    page_id = message["recipient"]["id"]
    attachments = message["message"].get("attachments", [])
    content_type = None
    media_url = None
    if len(attachments) > 0:
        attachment = attachments[0]
        media_url = attachment["payload"]["url"]
        content_type = attachment["type"]

    message = FacebookMessage(
        user_id=message["sender"]["id"],
        page_id=page_id,
        message_text=message["message"].get("text", ""),
        media_url=media_url,
        content_type=content_type,
    )
    experiment_channel = ExperimentChannel.objects.filter(extra_data__contains={"page_id": message.page_id}).first()
    if not experiment_channel:
        return
    message_handler = FacebookMessengerChannel(experiment_channel=experiment_channel)
    message_handler.new_user_message(message)
