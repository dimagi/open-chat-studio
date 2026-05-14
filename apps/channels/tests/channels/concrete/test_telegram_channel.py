from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.telegram_channel import TelegramChannel


@pytest.fixture()
def _patched_telebot():
    with patch("apps.channels.channels_v2.telegram_channel.TeleBot") as mock_telebot:
        yield mock_telebot


def _make_channel(_patched_telebot, *, experiment_session=None):
    experiment = MagicMock()
    experiment_channel = MagicMock()
    experiment_channel.extra_data = {"bot_token": "fake_token"}
    return TelegramChannel(
        experiment=experiment,
        experiment_channel=experiment_channel,
        experiment_session=experiment_session,
    )


class TestTelegramChannelInit:
    def test_bot_is_constructed_from_extra_data(self, _patched_telebot):
        channel = _make_channel(_patched_telebot)
        _patched_telebot.assert_called_once_with("fake_token", threaded=False)
        assert channel.telegram_bot is _patched_telebot.return_value

    def test_accepts_optional_session(self, _patched_telebot):
        session = MagicMock()
        channel = _make_channel(_patched_telebot, experiment_session=session)
        assert channel.experiment_session is session


class TestTelegramChannelCanSendFile:
    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected"),
        [
            ("image/jpeg", 5 * 1024 * 1024, True),
            ("image/jpeg", 15 * 1024 * 1024, False),  # over 10MB image limit
            ("video/mp4", 40 * 1024 * 1024, True),
            ("video/mp4", 60 * 1024 * 1024, False),  # over 50MB media limit
            ("application/pdf", 30 * 1024 * 1024, True),
            ("text/plain", 1 * 1024 * 1024, False),  # unsupported top-level type for telegram
        ],
    )
    def test_size_and_type_limits(self, _patched_telebot, content_type, content_size, expected):
        channel = _make_channel(_patched_telebot)
        file = MagicMock()
        file.content_type = content_type
        file.content_size = content_size
        assert channel._can_send_file(file) is expected

    def test_missing_content_type_returns_false(self, _patched_telebot):
        channel = _make_channel(_patched_telebot)
        file = MagicMock()
        file.content_type = None
        file.content_size = None
        assert channel._can_send_file(file) is False
