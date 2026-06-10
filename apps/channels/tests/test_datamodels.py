import pytest

from apps.channels.datamodels import BaseMessage, is_non_conversational_whatsapp_message
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
            turnio_messages.system_user_changed_number_message(),
            turnio_messages.unsupported_message(),
            meta_cloud_api_messages.system_user_changed_number_value(),
            meta_cloud_api_messages.unsupported_message_value(),
            # The "unsupported" type is reported as "unknown" on some Meta API versions.
            {"messages": [{"type": "unknown"}]},
        ],
    )
    def test_true_for_system_and_unsupported(self, message_data):
        assert is_non_conversational_whatsapp_message(message_data) is True

    @pytest.mark.parametrize(
        "message_data",
        [
            turnio_messages.text_message(),
            turnio_messages.audio_message(),
            meta_cloud_api_messages.text_message_value(),
            meta_cloud_api_messages.audio_message_value(),
        ],
    )
    def test_false_for_conversational(self, message_data):
        assert is_non_conversational_whatsapp_message(message_data) is False

    def test_false_when_no_messages(self):
        assert is_non_conversational_whatsapp_message({}) is False
        assert is_non_conversational_whatsapp_message({"messages": []}) is False
