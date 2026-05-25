import pytest

from apps.channels.datamodels import (
    BaseMessage,
    MetaCloudAPIMessage,
    TurnWhatsappMessage,
    TwilioMessage,
    _extract_wa_participant_id,
    looks_like_bsuid,
)
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


class TestExtractWaParticipantId:
    def test_prefers_contacts_wa_id(self):
        message_data = {"contacts": [{"wa_id": "27000000001"}]}
        message = {"from": "27000000002"}
        assert _extract_wa_participant_id(message_data, message) == "27000000001"

    def test_falls_back_to_message_from_when_contacts_missing(self):
        message_data = {}
        message = {"from": "27000000002"}
        assert _extract_wa_participant_id(message_data, message) == "27000000002"

    def test_falls_back_to_message_from_when_contacts_empty(self):
        message_data = {"contacts": []}
        message = {"from": "27000000002"}
        assert _extract_wa_participant_id(message_data, message) == "27000000002"

    def test_falls_back_to_system_wa_id_when_contacts_and_from_missing(self):
        message_data = {}
        message = {"system": {"wa_id": "27000000003"}}
        assert _extract_wa_participant_id(message_data, message) == "27000000003"

    def test_returns_none_when_no_identifiers_available(self):
        assert _extract_wa_participant_id({}, {}) is None

    def test_returns_none_when_contacts_entry_has_no_wa_id(self):
        """If contacts is present but the entry lacks wa_id, we should fall
        through to the next source rather than returning a stale value."""
        message_data = {"contacts": [{"profile": {"name": "User"}}]}
        message = {"from": "27000000004"}
        assert _extract_wa_participant_id(message_data, message) == "27000000004"


class TestTurnWhatsappMessageParse:
    def test_parse_text_message_returns_message(self):
        parsed = TurnWhatsappMessage.parse(turnio_messages.text_message())
        assert parsed is not None
        assert parsed.participant_id == "27456897512"
        assert parsed.message_text == "Hi there!"

    @pytest.mark.parametrize(
        "payload",
        [
            turnio_messages.system_user_changed_number_message(),
            turnio_messages.unsupported_message(),
        ],
    )
    def test_parse_non_conversational_returns_none(self, payload):
        """``system`` and ``unsupported`` payloads must short-circuit
        instead of raising a KeyError on the missing ``contacts`` key."""
        assert TurnWhatsappMessage.parse(payload) is None

    def test_parse_does_not_raise_when_contacts_missing(self):
        """Regression test for the KeyError fixed in this PR: a payload
        without a top-level ``contacts`` array used to crash on
        ``message_data["contacts"][0]["wa_id"]``."""
        payload = turnio_messages.text_message()
        del payload["contacts"]
        parsed = TurnWhatsappMessage.parse(payload)
        assert parsed is not None
        assert parsed.participant_id == "27456897512"

    def test_parse_returns_none_when_no_participant_id_resolvable(self):
        """A text payload missing contacts, ``from``, and ``system`` should
        be ignored rather than crash."""
        payload = {
            "messages": [
                {
                    "id": "wamid.no_participant",
                    "timestamp": "1706709716",
                    "type": "text",
                    "text": {"body": "Hello"},
                }
            ],
        }
        assert TurnWhatsappMessage.parse(payload) is None


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

    @pytest.mark.parametrize(
        "payload_value",
        [
            meta_cloud_api_messages.system_user_changed_number_value(),
            meta_cloud_api_messages.unsupported_message_value(),
        ],
    )
    def test_parse_non_conversational_returns_none(self, payload_value):
        """``system`` and ``unsupported`` payloads short-circuit before hitting
        the identifier-resolution logic that would otherwise KeyError on the
        missing ``from_user_id`` field for system events."""
        message = payload_value["messages"][0]
        assert MetaCloudAPIMessage.parse(message) is None


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


class TestLooksLikeBsuid:
    """Unit tests for the ``looks_like_bsuid`` predicate."""

    def test_bsuid_shapes_accepted(self):
        """Meta's BSUID spec: ISO 3166 alpha-2 country code + period + up to 128 alphanumeric
        characters. Parent BSUIDs (cross-portfolio) insert ``ENT.`` between country and identifier.
        """
        assert looks_like_bsuid("US.13491208655302741918") is True
        assert looks_like_bsuid("US.ENT.11815799212886844830") is True
        assert looks_like_bsuid("ZA.abc123XYZ") is True  # Alphanumeric tail, not just digits.
        assert looks_like_bsuid("US." + "a" * 128) is True  # Spec maximum.

    def test_non_bsuid_strings_are_rejected(self):
        """Locale-formatted phones and other not-quite-BSUID strings must not pass."""
        assert looks_like_bsuid("+1.212.555.2368") is False
        assert looks_like_bsuid("us.13491208655302741918") is False  # Lowercase country.
        assert looks_like_bsuid("USA.13491208655302741918") is False  # 3-letter country.
        assert looks_like_bsuid("US.13491208655302741918.extra") is False  # Tail contains period.
        assert looks_like_bsuid("US.has-dash") is False  # Non-alphanumeric in tail.
        assert looks_like_bsuid("US." + "a" * 129) is False  # Tail exceeds 128 chars.
        assert looks_like_bsuid("US.") is False  # Empty tail.
        assert looks_like_bsuid("27456897512") is False  # Plain wa_id.
        assert looks_like_bsuid("+27456897512") is False  # E.164.
        assert looks_like_bsuid("") is False
