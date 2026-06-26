import pytest

from apps.channels.datamodels import (
    BaseMessage,
    TwilioMessage,
    WhatsAppMessage,
    is_non_conversational_whatsapp_message,
    looks_like_bsuid,
)
from apps.channels.models import ChannelPlatform
from apps.channels.tests.message_examples import meta_cloud_api_messages, turnio_messages


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


class TestIsNonConversationalWhatsAppMessage:
    """The webhook views use this to skip non-conversational payloads before
    dispatching a Celery task. "system" payloads omit the ``contacts`` array and
    would crash the task; "unsupported"/"unknown" payloads include ``contacts`` but
    carry nothing conversational to process."""

    @pytest.mark.parametrize(
        "message_data",
        [
            pytest.param(turnio_messages.system_user_changed_number_message(), id="turnio_system"),
            pytest.param(turnio_messages.unsupported_message(), id="turnio_unsupported"),
            pytest.param(meta_cloud_api_messages.system_user_changed_number_value(), id="meta_system"),
            pytest.param(meta_cloud_api_messages.unsupported_message_value(), id="meta_unsupported"),
            # The "unsupported" type is reported as "unknown" on some Meta API versions.
            pytest.param({"messages": [{"type": "unknown"}]}, id="meta_unknown_type"),
        ],
    )
    def test_true_for_system_and_unsupported(self, message_data):
        assert is_non_conversational_whatsapp_message(message_data) is True

    @pytest.mark.parametrize(
        "message_data",
        [
            pytest.param(turnio_messages.text_message(), id="turnio_text"),
            pytest.param(turnio_messages.audio_message(), id="turnio_audio"),
            pytest.param(meta_cloud_api_messages.text_message_value(), id="meta_text"),
            pytest.param(meta_cloud_api_messages.audio_message_value(), id="meta_audio"),
        ],
    )
    def test_false_for_conversational(self, message_data):
        assert is_non_conversational_whatsapp_message(message_data) is False

    def test_false_when_no_messages(self):
        assert is_non_conversational_whatsapp_message({}) is False
        assert is_non_conversational_whatsapp_message({"messages": []}) is False


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


class TestWhatsAppMessageParseBSUID:
    """When the webhook carries a BSUID (from_user_id) it is the participant_id and the phone
    (wa_id) is captured separately for sending. When it doesn't (Turn.io, pre-rollout traffic),
    the participant_id falls back to the phone, as participants were historically keyed."""

    BSUID = "US.13491208655302741918"

    def test_meta_payload_uses_bsuid_as_participant_id_and_captures_phone(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.text_message_value())
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number == "27456897512"

    def test_turnio_payload_uses_bsuid_as_participant_id_and_captures_phone(self):
        parsed = WhatsAppMessage.parse(turnio_messages.text_message())
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number == "27456897512"

    def test_falls_back_to_phone_when_no_bsuid(self):
        message_data = {
            "contacts": [{"wa_id": "27456897512", "profile": {"name": "User"}}],
            "messages": [{"from": "27456897512", "id": "x", "timestamp": "1", "type": "text", "text": {"body": "Hi"}}],
        }
        parsed = WhatsAppMessage.parse(message_data)
        assert parsed.participant_id == "27456897512"
        assert parsed.phone_number == "27456897512"


class TestTwilioMessageParseBSUID:
    BUSINESS = "whatsapp:+14155238886"
    BSUID = "US.13491208655302741918"

    def test_whatsapp_uses_external_user_id_bsuid_and_captures_phone(self):
        message = {
            "From": "whatsapp:+27456897512",
            "To": self.BUSINESS,
            "Body": "Hello",
            "ExternalUserId": f"whatsapp:{self.BSUID}",
        }
        parsed = TwilioMessage.parse(message)
        assert parsed.participant_id == self.BSUID
        assert parsed.phone_number == "+27456897512"

    def test_whatsapp_without_external_user_id_falls_back_to_phone(self):
        """Pre-rollout Twilio webhooks carry no ExternalUserId; the phone becomes the
        participant_id, matching how participants were historically keyed."""
        message = {"From": "whatsapp:+27456897512", "To": self.BUSINESS, "Body": "Hello"}
        parsed = TwilioMessage.parse(message)
        assert parsed.participant_id == "+27456897512"
        assert parsed.phone_number == "+27456897512"

    def test_facebook_messenger_has_no_phone_number(self):
        message = {"From": "messenger:1234567890", "To": "messenger:9876543210", "Body": "Hi"}
        parsed = TwilioMessage.parse(message)
        assert parsed.platform == ChannelPlatform.FACEBOOK
        assert parsed.participant_id == "1234567890"
        assert parsed.phone_number is None
