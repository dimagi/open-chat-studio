from unittest.mock import Mock, patch

import pytest
from telebot.apihelper import ApiTelegramException

from apps.channels.models import ExperimentChannel
from apps.chat.channels import TelegramChannel
from apps.chat.exceptions import ChannelException
from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    Participant,
    ParticipantData,
)
from apps.files.models import File
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.fixture()
def telegram_channel():
    experiment = Mock()
    session = Mock()
    session.id = 123
    experiment_channel = Mock()
    experiment_channel.extra_data = {"bot_token": "fake_token"}
    return TelegramChannel(experiment, experiment_channel=experiment_channel, experiment_session=session)


def make_mock_file(name, content_type, size, file_data="file_data"):
    file = Mock(spec=File)
    file.name = name
    file.content_type = content_type
    file.content_size = size
    file.file = file_data
    file.download_link.return_value = f"http://example.com/{name}"
    return file


@pytest.mark.django_db()
def test_handle_telegram_block_updates_consent():
    experiment: Experiment = ExperimentFactory()  # ty: ignore[invalid-assignment]
    exp_channel: ExperimentChannel = ExperimentChannelFactory(  # ty: ignore[invalid-assignment]
        experiment=experiment, extra_data={"bot_token": "fake_token"}
    )
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
    experiment: Experiment = ExperimentFactory()  # ty: ignore[invalid-assignment]
    exp_channel: ExperimentChannel = ExperimentChannelFactory(  # ty: ignore[invalid-assignment]
        experiment=experiment, extra_data={"bot_token": "fake_token"}
    )
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
    exp_channel: ExperimentChannel = ExperimentChannelFactory(  # ty: ignore[invalid-assignment]
        experiment=experiment, extra_data={"bot_token": "fake_token"}
    )
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


def test_supported_image_file(telegram_channel):
    image_file = make_mock_file("img.jpg", "image/jpeg", 5 * 1024 * 1024)

    with (
        patch.object(telegram_channel.telegram_bot, "send_photo") as mock_send_photo,
        patch.object(telegram_channel, "send_text_to_user") as mock_send_text,
    ):
        telegram_channel.send_message_to_user("Here is your file", files=[image_file])

        mock_send_photo.assert_called_once()
        mock_send_text.assert_called_once_with("Here is your file")


def test_large_image_fallback_to_text(telegram_channel):
    channel = telegram_channel
    large_image = make_mock_file("big_img.jpg", "image/jpeg", 15 * 1024 * 1024)

    with (
        patch.object(channel, "send_text_to_user") as mock_send_text,
        patch.object(channel, "_send_files_to_user") as mock_send_file,
    ):
        channel.send_message_to_user("Here is your file", files=[large_image])

        mock_send_file.assert_not_called()

        expected_message = "Here is your file\n\nbig_img.jpg\nhttp://example.com/big_img.jpg\n"
        mock_send_text.assert_called_once_with(expected_message)


def test_unsupported_mime_file(telegram_channel):
    channel = telegram_channel
    exe_file = make_mock_file("script.exe", "application/octet-stream", 1 * 1024 * 1024)

    with (
        patch.object(channel, "_send_files_to_user") as mock_send_file,
        patch.object(channel, "send_text_to_user") as mock_send_text,
    ):
        channel.send_message_to_user("Please find the file", files=[exe_file])
        mock_send_file.assert_called_once_with([exe_file])
        mock_send_text.assert_called_once_with("Please find the file")
