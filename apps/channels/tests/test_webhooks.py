from unittest.mock import patch

from django.conf import settings

from apps.channels.webhooks import TelegramWebhookManager


@patch("apps.channels.webhooks.TeleBot")
def test_set_incoming_webhook_sets_webhook_and_commands(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    manager.set_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with("https://example.com/hook", secret_token=settings.TELEGRAM_SECRET_TOKEN)
    bot.set_my_commands.assert_called_once()


@patch("apps.channels.webhooks.TeleBot")
def test_remove_incoming_webhook_clears_webhook(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    manager.remove_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with(None)


def test_supports_webhook_management():
    assert TelegramWebhookManager.supports_webhook_management is True
