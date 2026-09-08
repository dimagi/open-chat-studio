from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from apps.channels.models import ChannelPlatform
from apps.service_providers.messaging_service import TwilioService, _utf16_len, utf16_aware_split

SIREN = "\U0001f6a8"  # outside the BMP: 1 code point, 2 UTF-16 code units
EM_DASH = "—"  # inside the BMP: 1 code point, 1 UTF-16 code unit
TWILIO_LIMIT = 1600  # UTF-16 code units, enforced by Twilio


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", 0, id="empty"),
        pytest.param("abc", 3, id="ascii"),
        pytest.param(EM_DASH, 1, id="bmp-char-is-one-unit"),
        pytest.param(SIREN, 2, id="astral-char-is-two-units"),
        pytest.param(f"a{SIREN}b", 4, id="mixed"),
    ],
)
def test_utf16_len(text, expected):
    assert _utf16_len(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("hello", id="short"),
        pytest.param(SIREN * 5, id="exactly-at-limit"),
    ],
)
def test_text_within_limit_is_not_split(text):
    assert utf16_aware_split(text, limit=10) == [text]


def test_splits_at_newline_boundaries():
    text = "line one\nline two\nline three\n"
    assert utf16_aware_split(text, limit=18) == ["line one\nline two\n", "line three\n"]


def test_splits_at_word_boundaries_when_a_line_is_too_long():
    assert utf16_aware_split("alpha beta gamma delta", limit=12) == ["alpha beta ", "gamma delta"]


def test_hard_splits_a_word_longer_than_the_limit():
    assert utf16_aware_split("abcdefghij", limit=4) == ["abcd", "efgh", "ij"]


def test_astral_characters_count_as_two_units():
    """Six emoji are 6 code points but 12 UTF-16 units, so a 10-unit limit must split them."""
    text = SIREN * 6
    assert len(text) < 10 < _utf16_len(text)
    assert utf16_aware_split(text, limit=10) == [SIREN * 5, SIREN]


def test_emoji_heavy_message_within_python_len_is_still_split():
    """Regression: ``len()`` sees 1600 characters, Twilio sees 1800 UTF-16 units."""
    message = f"{SIREN} alert " * 200
    assert len(message) == 1600
    assert _utf16_len(message) == 1800

    chunks = utf16_aware_split(message, limit=TWILIO_LIMIT)
    assert len(chunks) > 1
    assert all(_utf16_len(chunk) <= TWILIO_LIMIT for chunk in chunks)
    assert "".join(chunks) == message


@pytest.mark.parametrize(
    ("text", "limit"),
    [
        pytest.param("a\nb\nc", 3, id="short-lines"),
        pytest.param("alpha beta gamma delta", 12, id="word-boundaries"),
        pytest.param("abcdefghij", 4, id="no-boundaries"),
        pytest.param("aaa bbb", 3, id="every-word-over-limit"),
        pytest.param("a\n\nb", 2, id="blank-line"),
        pytest.param(SIREN * 25, 6, id="only-emoji"),
        pytest.param(f"{SIREN} alert {EM_DASH} take cover\n" * 40, 100, id="mixed-lines-and-emoji"),
        pytest.param(f"{SIREN}{'x' * 500} " * 10, 1600, id="long-words"),
    ],
)
def test_split_is_lossless_and_within_limit(text, limit):
    chunks = utf16_aware_split(text, limit=limit)
    assert "".join(chunks) == text
    assert all(chunk for chunk in chunks)
    assert all(_utf16_len(chunk) <= limit for chunk in chunks)


class TestTwilioSendTextMessage:
    def _send(self, message):
        service = TwilioService(account_sid="test", auth_token="test")
        assert service.MESSAGE_CHARACTER_LIMIT == TWILIO_LIMIT
        with patch.object(TwilioService, "client", new_callable=PropertyMock) as client:
            client.return_value = MagicMock()
            # block_until_delivered polls with a backoff until the chunk is delivered
            client.return_value.messages.get.return_value.fetch.return_value.status = "delivered"
            service.send_text_message(
                message=message,
                from_="+27000000000",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
            )
            return client.return_value.messages

    def test_short_message_sent_as_one(self):
        messages = self._send(f"All clear {SIREN}")
        assert messages.create.call_count == 1
        assert messages.create.call_args.kwargs["body"] == f"All clear {SIREN}"
        messages.get.assert_not_called()

    def test_emoji_heavy_message_is_chunked_within_the_utf16_limit(self):
        message = f"{SIREN} alert " * 200
        messages = self._send(message)

        bodies = [call.kwargs["body"] for call in messages.create.call_args_list]
        assert len(bodies) > 1
        assert all(_utf16_len(body) <= TWILIO_LIMIT for body in bodies)
        assert "".join(bodies) == message
        assert messages.get.call_count == len(bodies)
