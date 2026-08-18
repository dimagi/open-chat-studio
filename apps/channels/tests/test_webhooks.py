from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.channels.webhooks import TelegramWebhookManager
from apps.utils.factories.channels import ExperimentChannelFactory


@patch("apps.channels.webhooks.TeleBot")
def test_set_incoming_webhook_sets_webhook_and_commands(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    with override_settings(TELEGRAM_SECRET_TOKEN="my-secret-token"):
        manager.set_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with("https://example.com/hook", secret_token="my-secret-token")
    bot.set_my_commands.assert_called_once()


@patch("apps.channels.webhooks.TeleBot")
def test_set_incoming_webhook_passes_none_when_secret_unset(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    with override_settings(TELEGRAM_SECRET_TOKEN=""):
        manager.set_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with("https://example.com/hook", secret_token=None)


@patch("apps.channels.webhooks.TeleBot")
def test_remove_incoming_webhook_clears_webhook(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    manager.remove_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with(None)


def test_supports_webhook_management():
    assert TelegramWebhookManager.supports_webhook_management is True


@pytest.fixture()
def telegram_channel(db):
    return ExperimentChannelFactory.create(platform=ChannelPlatform.TELEGRAM)


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_telegram_message.delay")
def test_telegram_message_accepted_when_secret_unset(mock_handle, client, telegram_channel):
    with override_settings(TELEGRAM_SECRET_TOKEN=""):
        response = client.post(
            reverse("channels:new_telegram_message", args=[telegram_channel.external_id]),
            data="{}",
            content_type="application/json",
        )
    assert response.status_code == 200
    mock_handle.assert_called_once()


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_telegram_message.delay")
def test_telegram_message_accepted_with_valid_secret(mock_handle, client, telegram_channel):
    with override_settings(TELEGRAM_SECRET_TOKEN="valid-secret"):
        response = client.post(
            reverse("channels:new_telegram_message", args=[telegram_channel.external_id]),
            data="{}",
            content_type="application/json",
            headers={"x-telegram-bot-api-secret-token": "valid-secret"},
        )
    assert response.status_code == 200
    mock_handle.assert_called_once()


@pytest.mark.django_db()
def test_telegram_message_rejected_with_invalid_secret(client, telegram_channel):
    with override_settings(TELEGRAM_SECRET_TOKEN="valid-secret"):
        response = client.post(
            reverse("channels:new_telegram_message", args=[telegram_channel.external_id]),
            data="{}",
            content_type="application/json",
            headers={"x-telegram-bot-api-secret-token": "wrong-secret"},
        )
    assert response.status_code == 400
    assert response.content == b"Invalid request."


@pytest.mark.django_db()
def test_telegram_message_rejected_when_secret_missing_but_required(client, telegram_channel):
    with override_settings(TELEGRAM_SECRET_TOKEN="valid-secret"):
        response = client.post(
            reverse("channels:new_telegram_message", args=[telegram_channel.external_id]),
            data="{}",
            content_type="application/json",
        )
    assert response.status_code == 400
    assert response.content == b"Invalid request."

