from unittest.mock import Mock, patch

import pytest
from telebot.apihelper import ApiTelegramException

from apps.chat.channels import TelegramChannel
from apps.chat.exceptions import ChannelException
from apps.experiments.models import (
    ExperimentSession,
    Participant,
    ParticipantData,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_handle_telegram_block_updates_consent():
    experiment = ExperimentFactory()
    exp_channel = ExperimentChannelFactory(experiment=experiment, extra_data={"bot_token": "fake_token"})
    participant_identifier = "test_telegram_user_id_for_error_test"
    participant = Participant.objects.create(
        identifier=participant_identifier,
        team=experiment.team,
    )
    session = ExperimentSession.objects.create(
        experiment=experiment,
        participant=participant,
        experiment_channel=exp_channel,
        team=experiment.team,
    )
    participant_data = ParticipantData.objects.create(
        participant=participant,
        experiment=experiment,
        system_metadata={"consent": True},
        team=experiment.team,
    )
    channel = TelegramChannel(experiment=experiment, experiment_channel=exp_channel, experiment_session=session)
    channel.telegram_bot = Mock()
    error = ApiTelegramException(
        function_name="send_message",
        result_json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        result=None,
    )

    channel._handle_telegram_api_error(error)

    participant_data.refresh_from_db()
    assert participant_data.system_metadata.get("consent") is False


@pytest.mark.django_db()
def test_handle_telegram_block_participant_data_does_not_exist():
    experiment = ExperimentFactory()
    exp_channel = ExperimentChannelFactory(experiment=experiment, extra_data={"bot_token": "fake_token"})
    channel = TelegramChannel(experiment=experiment, experiment_channel=exp_channel)
    channel.telegram_bot = Mock()

    error = ApiTelegramException(
        function_name="send_message",
        result_json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        result=None,
    )
    with pytest.raises(ChannelException) as excinfo:
        channel._handle_telegram_api_error(error)
    assert "Participant data does not exist during consent update" in str(excinfo.value)


@pytest.mark.django_db()
@patch("apps.chat.channels.TeleBot", Mock)
def test_handle_telegram_api_error_other_errors():
    experiment = ExperimentFactory()
    exp_channel = ExperimentChannelFactory(experiment=experiment, extra_data={"bot_token": "fake_token"})
    channel = TelegramChannel(experiment=exp_channel.experiment, experiment_channel=exp_channel)
    channel.telegram_bot = Mock()
    error_description = "Internal Server Error: something went wrong on Telegram's side"
    api_error = ApiTelegramException(
        function_name="sendMessage",
        result_json={"ok": False, "error_code": 500, "description": error_description},
        result=None,
    )
    expected_channel_exception_message = f"Telegram API error occurred: {api_error.description}"

    with pytest.raises(ChannelException) as excinfo:
        channel._handle_telegram_api_error(api_error)

    assert expected_channel_exception_message == str(excinfo.value)
    assert excinfo.value.__cause__ == api_error
