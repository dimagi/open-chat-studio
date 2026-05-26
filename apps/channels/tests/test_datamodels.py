import pytest

from apps.channels.datamodels import BaseMessage, MetaCloudAPIMessage, TwilioMessage, looks_like_bsuid
from apps.channels.tests.message_examples import meta_cloud_api_messages


class TestBaseMessage:
    def test_default_attachment_file_ids_empty(self):
        msg = BaseMessage(participant_id="u1", message_text="hi")
        assert msg.attachment_file_ids == []

    def test_attachment_file_ids_serialized(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[1, 2, 3])
        dumped = msg.model_dump()
        assert dumped["attachment_file_ids"] == [1, 2, 3]

    def test_attachment_file_ids_round_trip(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[42])
        rebuilt = BaseMessage(**msg.model_dump())
        assert rebuilt.attachment_file_ids == [42]


class TestMetaCloudAPIMessageParse:
    """Parse-time behavior of inbound Meta Cloud API webhook messages.

    Currently the phone number (``from``) is used as ``participant_id`` when present;
    ``from_user_id`` (BSUID) is used only as a fallback when the phone is absent.
    Tests for BSUID-as-primary-identifier are deferred until Meta supports BSUID
    in outbound message payloads.
    """

    BSUID = "US.13491208655302741918"

    def test_payload_without_phone_captures_only_bsuid(self):
        """Username-adopter whose phone has been hidden by Meta. No ``from`` field."""
        message = {
            "from_user_id": self.BSUID,
            "id": "wamid.abc123",
            "timestamp": "1706709716",
            "text": {"body": "Hello"},
            "type": "text",
        }
        parsed = MetaCloudAPIMessage.parse(message)
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number is None

    def test_audio_payload_preserves_media_id_and_message_id(self):
        message = meta_cloud_api_messages.audio_message_value()["messages"][0]
        parsed = MetaCloudAPIMessage.parse(message)
        assert parsed.media_id == "1215194677037265"
        assert parsed.whatsapp_message_id == "wamid.abc456"


class TestTwilioMessageParse:
    """Parse-time behavior of inbound Twilio WhatsApp webhook messages.

    Tests for BSUID-as-primary-identifier are deferred until Twilio/Meta support
    BSUID in outbound message payloads.
    """

    BUSINESS = "whatsapp:+14155238886"

    def test_parse_raises_when_external_user_id_missing(self):
        """Post-rollout Twilio guarantees ``ExternalUserId`` on every WhatsApp webhook —
        its absence is a malformed payload and should fail loudly."""
        message = {
            "From": "whatsapp:+27456897512",
            "To": self.BUSINESS,
            "Body": "Hello",
        }
        with pytest.raises(KeyError):
            TwilioMessage.parse(message)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("US.13491208655302741918", True),
        ("US.ENT.11815799212886844830", True),
        ("ZA.abc123XYZ", True),  # Alphanumeric tail, not just digits.
        ("US." + "a" * 128, True),  # Spec maximum.
        ("+1.212.555.2368", False),
        ("us.13491208655302741918", False),  # Lowercase country.
        ("USA.13491208655302741918", False),  # 3-letter country.
        ("US.13491208655302741918.extra", False),  # Tail contains period.
        ("US.has-dash", False),  # Non-alphanumeric in tail.
        ("US." + "a" * 129, False),  # Tail exceeds 128 chars.
        ("US.", False),  # Empty tail.
        ("27456897512", False),  # Plain wa_id.
        ("+27456897512", False),  # E.164.
        ("", False),
    ],
)
def test_looks_like_bsuid(value, expected):
    assert looks_like_bsuid(value) is expected
