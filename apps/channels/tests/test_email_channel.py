from unittest.mock import MagicMock

from apps.channels.datamodels import EmailMessage


def _make_inbound_message(
    from_email="sender@example.com",
    to_email="bot@chat.openchatstudio.com",
    subject="Hello",
    text="Hi there",
    message_id="<msg1@example.com>",
    in_reply_to=None,
    references="",
):
    """Create a mock AnymailInboundMessage."""
    msg = MagicMock()
    msg.from_email.addr_spec = from_email
    msg.to = [MagicMock()]
    msg.to[0].addr_spec = to_email
    msg.subject = subject
    msg.text = text
    msg.get = MagicMock(
        side_effect=lambda key, default=None: {
            "Message-ID": message_id,
            "In-Reply-To": in_reply_to,
            "References": references,
        }.get(key, default)
    )
    return msg


class TestEmailMessageParse:
    def test_basic_parse(self):
        inbound = _make_inbound_message()
        result = EmailMessage.parse(inbound)

        assert result.participant_id == "sender@example.com"
        assert result.message_text == "Hi there"
        assert result.from_address == "sender@example.com"
        assert result.to_address == "bot@chat.openchatstudio.com"
        assert result.subject == "Hello"
        assert result.message_id == "<msg1@example.com>"
        assert result.in_reply_to is None
        assert result.references == []

    def test_parse_with_reply_headers(self):
        inbound = _make_inbound_message(
            in_reply_to="<original@example.com>",
            references="<original@example.com> <reply1@example.com>",
        )
        result = EmailMessage.parse(inbound)

        assert result.in_reply_to == "<original@example.com>"
        assert result.references == ["<original@example.com>", "<reply1@example.com>"]

    def test_parse_strips_quoted_text(self):
        body_with_quote = "New reply text\n\nOn Mon, Apr 21, 2026, user wrote:\n> Original message"
        inbound = _make_inbound_message(text=body_with_quote)
        result = EmailMessage.parse(inbound)

        # mail-parser-reply should strip the quoted portion
        assert "Original message" not in result.message_text
        assert "New reply text" in result.message_text

    def test_parse_no_to_address(self):
        inbound = _make_inbound_message()
        inbound.to = []
        result = EmailMessage.parse(inbound)
        assert result.to_address == ""

    def test_parse_empty_subject(self):
        inbound = _make_inbound_message(subject=None)
        result = EmailMessage.parse(inbound)
        assert result.subject == ""

    def test_parse_empty_references(self):
        inbound = _make_inbound_message(references="")
        result = EmailMessage.parse(inbound)
        assert result.references == []

    def test_parse_fallback_to_full_body_when_no_reply(self):
        """When there is no quoted text, the full body is preserved."""
        inbound = _make_inbound_message(text="Just a simple message")
        result = EmailMessage.parse(inbound)
        assert result.message_text == "Just a simple message"
