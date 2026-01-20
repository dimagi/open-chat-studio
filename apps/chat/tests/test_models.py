import pytest

from apps.annotations.models import TagCategories
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_get_attached_files():
    assistant = OpenAiAssistantFactory()
    session = ExperimentSessionFactory()
    assistant_file1 = FileFactory(external_id="assistant-file-id-1", team=session.chat.team)
    assistant_file2 = FileFactory(external_id="assistant-file-id-2", team=session.chat.team)
    tool_resource = assistant.tool_resources.create(tool_type="code_interpreter")
    tool_resource.files.add(*[assistant_file1, assistant_file2])

    chat_file1 = FileFactory(external_id="chat-file-id-1", team=session.chat.team)
    chat_file2 = FileFactory(external_id="chat-file-id-2", team=session.chat.team)
    chat = session.chat
    attachment = chat.attachments.create(tool_type="code_interpreter")
    attachment.files.add(*[chat_file1, chat_file2])

    # Add message with a reference to both the chat and assistant level files
    metadata = {
        "openai_file_ids": ["assistant-file-id-1", "chat-file-id-1", "assistant-file-id-2", "chat-file-id-2"],
    }
    message = ChatMessage.objects.create(chat=chat, message_type="ai", content="Hi", metadata=metadata)
    files = message.get_attached_files()
    assert chat_file1 in files
    assert chat_file2 in files
    assert assistant_file1 not in files
    assert assistant_file2 not in files


@pytest.mark.django_db()
class TestChatMessage:
    def test_get_processor_bot_tag_name(self):
        session = ExperimentSessionFactory()
        human_message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi")
        ai_message_wo_tag = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi")
        ai_message_with_tag = ChatMessage.objects.create(
            chat=session.chat, message_type=ChatMessageType.AI, content="Hi"
        )
        ai_message_with_tag.create_and_add_tag("some-bot", session.team, tag_category=TagCategories.BOT_RESPONSE)

        assert human_message.get_processor_bot_tag_name() is None
        assert ai_message_wo_tag.get_processor_bot_tag_name() is None
        assert ai_message_with_tag.get_processor_bot_tag_name() == "some-bot"

    def test_add_version_tag(self):
        session = ExperimentSessionFactory()
        chat_message1 = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi")
        chat_message2 = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi")
        chat_message1.add_version_tag(version_number=1, is_a_version=True)
        chat_message2.add_version_tag(version_number=1, is_a_version=False)

        assert chat_message1.tags.first().name == "v1"
        assert chat_message2.tags.first().name == "v1-unreleased"
