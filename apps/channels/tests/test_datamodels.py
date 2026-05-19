import pytest

from apps.channels.datamodels import BaseMessage, MetaCloudAPIMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
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

    ``participant_id`` is sourced strictly from ``from_user_id`` (the BSUID). The phone
    number — when present in ``from`` — is captured separately as ``phone_number`` so the
    channel can match a legacy phone-keyed Participant during the BSUID rollout.
    """

    BSUID = "US.13491208655302741918"
    PHONE = "27456897512"

    def test_payload_with_phone_captures_both_bsuid_and_phone(self):
        """Webhook has BOTH ``from_user_id`` (BSUID) and ``from`` (phone)."""
        message = {
            "from_user_id": self.BSUID,
            "from": self.PHONE,
            "id": "wamid.abc123",
            "timestamp": "1706709716",
            "text": {"body": "Hello"},
            "type": "text",
        }
        parsed = MetaCloudAPIMessage.parse(message)
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number == self.PHONE

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

    def test_parse_raises_when_from_user_id_missing(self):
        """Post-rollout Meta guarantees ``from_user_id`` on every webhook — its absence is
        a malformed payload and should fail loudly."""
        message = {
            "from": "27456897512",
            "id": "wamid.abc123",
            "timestamp": "1706709716",
            "text": {"body": "Hello"},
            "type": "text",
        }
        with pytest.raises(KeyError):
            MetaCloudAPIMessage.parse(message)


class TestTwilioMessageParse:
    """Parse-time behavior of inbound Twilio WhatsApp webhook messages.

    Twilio guarantees ``ExternalUserId`` (BSUID) on every WhatsApp webhook post-rollout.
    The ``From`` field carries the phone number for non-username-adopters and the BSUID
    for username-adopters. We always source ``participant_id`` from ``ExternalUserId``;
    the phone — when From is a real phone — is normalized to E.164 and captured as
    ``phone_number``.
    """

    BSUID = "US.13491208655302741918"
    BUSINESS = "whatsapp:+14155238886"

    def test_payload_with_phone_captures_both_bsuid_and_phone(self):
        """Standard post-rollout webhook. From has the phone, ExternalUserId has the BSUID."""
        message = {
            "From": "whatsapp:+27456897512",
            "To": self.BUSINESS,
            "ExternalUserId": f"whatsapp:{self.BSUID}",
            "Body": "Hello",
        }
        parsed = TwilioMessage.parse(message)
        assert parsed.platform == ChannelPlatform.WHATSAPP
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number == "+27456897512"

    def test_payload_without_phone_captures_only_bsuid(self):
        """Username-adopter. ``From`` mirrors the BSUID; no phone is exposed."""
        message = {
            "From": f"whatsapp:{self.BSUID}",
            "To": self.BUSINESS,
            "ExternalUserId": f"whatsapp:{self.BSUID}",
            "Body": "Hello",
        }
        parsed = TwilioMessage.parse(message)
        assert parsed.platform == ChannelPlatform.WHATSAPP
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number is None

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
