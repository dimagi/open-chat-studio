import json
from unittest.mock import patch

import pytest

from apps.channels.tasks import handle_telegram_message
from apps.utils.factories.channels import ExperimentChannelFactory


def _edited_message_update(chat_id: int = 123) -> str:
    """A Telegram ``edited_message`` update. These carry no top-level ``message`` object."""
    return json.dumps(
        {
            "update_id": 432101234,
            "edited_message": {
                "message_id": 576,
                "from": {"id": chat_id, "is_bot": False, "first_name": "John"},
                "chat": {"id": chat_id, "first_name": "John", "type": "private"},
                "date": 1690376696,
                "edit_date": 1690376700,
                "text": "Edited text",
            },
        }
    )


def _my_chat_member_update(chat_id: int = 123) -> str:
    """A Telegram ``my_chat_member`` update (bot added/removed from a chat)."""
    return json.dumps(
        {
            "update_id": 432101235,
            "my_chat_member": {
                "chat": {"id": chat_id, "first_name": "John", "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "John"},
                "date": 1690376696,
                "old_chat_member": {"user": {"id": 999, "is_bot": True, "first_name": "Bot"}, "status": "left"},
                "new_chat_member": {"user": {"id": 999, "is_bot": True, "first_name": "Bot"}, "status": "member"},
            },
        }
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "message_data",
    [
        pytest.param(_edited_message_update(), id="edited_message"),
        pytest.param(_my_chat_member_update(), id="my_chat_member"),
    ],
)
def test_handle_telegram_message_ignores_non_message_updates(message_data):
    """``edited_message`` and ``my_chat_member`` updates carry no new user input and must be
    ignored before ``TelegramMessage.parse`` runs.

    Regression test for ``AttributeError: 'NoneType' object has no attribute 'chat'``, which
    happened because ``edited_message`` updates have no top-level ``message`` object.
    """
    experiment_channel = ExperimentChannelFactory()

    with (
        patch("apps.channels.tasks.TelegramMessage.parse") as parse,
        patch("apps.channels.tasks.TelegramChannel") as telegram_channel,
    ):
        handle_telegram_message(message_data, channel_external_id=experiment_channel.external_id)

    parse.assert_not_called()
    telegram_channel.assert_not_called()


@pytest.mark.django_db()
def test_handle_telegram_message_processes_normal_message():
    """A regular ``message`` update is still parsed and dispatched to the channel."""
    experiment_channel = ExperimentChannelFactory()
    message_data = json.dumps(
        {
            "update_id": 432101236,
            "message": {
                "message_id": 577,
                "from": {"id": 123, "is_bot": False, "first_name": "John"},
                "chat": {"id": 123, "first_name": "John", "type": "private"},
                "date": 1690376696,
                "text": "Hi there",
            },
        }
    )

    with (
        patch("apps.channels.tasks.TelegramMessage.parse") as parse,
        patch("apps.channels.tasks.TelegramChannel") as telegram_channel,
        patch("apps.channels.tasks.update_taskbadger_data"),
        patch("apps.channels.tasks.resolve_published_or_working"),
    ):
        handle_telegram_message(message_data, channel_external_id=experiment_channel.external_id)

    parse.assert_called_once()
    telegram_channel.return_value.new_user_message.assert_called_once_with(parse.return_value)
