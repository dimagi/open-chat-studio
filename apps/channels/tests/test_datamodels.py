from apps.channels.datamodels import BaseMessage, MetaCloudAPIMessage
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
    def test_legacy_payload_uses_phone_as_participant_id(self):
        message = meta_cloud_api_messages.legacy_text_message_value()["messages"][0]
        msg = MetaCloudAPIMessage.parse(message)
        assert msg.participant_id == "27456897512"

    def test_dual_field_payload_prefers_phone(self):
        message = meta_cloud_api_messages.text_message_with_user_id_and_wa_id_value()["messages"][0]
        msg = MetaCloudAPIMessage.parse(message)
        assert msg.participant_id == "27456897512"

    def test_username_adopter_with_phone_prefers_phone(self):
        message = meta_cloud_api_messages.text_message_with_username_and_wa_id_value()["messages"][0]
        msg = MetaCloudAPIMessage.parse(message)
        assert msg.participant_id == "27456897512"

    def test_user_id_only_payload_falls_back_to_user_id(self):
        message = meta_cloud_api_messages.text_message_user_id_only_value()["messages"][0]
        msg = MetaCloudAPIMessage.parse(message)
        assert msg.participant_id == "US.13491208655302741918"
