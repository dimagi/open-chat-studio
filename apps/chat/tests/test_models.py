import pytest

from apps.annotations.models import TagCategories
from apps.chat.models import ChatMessage, ChatMessageType
from apps.pipelines.models import PipelineChatHistoryModes
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER


@pytest.mark.django_db()
def test_get_attached_files():
    assistant = OpenAiAssistantFactory.create()
    session = ExperimentSessionFactory.create()
    assistant_file1 = FileFactory.create(external_id="assistant-file-id-1", team=session.chat.team)
    assistant_file2 = FileFactory.create(external_id="assistant-file-id-2", team=session.chat.team)
    tool_resource = assistant.tool_resources.create(tool_type="code_interpreter")
    tool_resource.files.add(*[assistant_file1, assistant_file2])

    chat_file1 = FileFactory.create(external_id="chat-file-id-1", team=session.chat.team)
    chat_file2 = FileFactory.create(external_id="chat-file-id-2", team=session.chat.team)
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
        session = ExperimentSessionFactory.create()
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
        session = ExperimentSessionFactory.create()
        chat_message1 = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi")
        chat_message2 = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi")
        chat_message1.add_version_tag(version_number=1, is_a_version=True)
        chat_message2.add_version_tag(version_number=1, is_a_version=False)

        assert chat_message1.tags.first().name == "v1"
        assert chat_message2.tags.first().name == "v1-unreleased"

    def test_rating_unsaved_instance_returns_none(self):
        """ChatMessage.rating() must return None (not raise) when the instance has no pk."""
        message = ChatMessage(message_type=ChatMessageType.AI, content="Hi")
        assert message.rating() is None


@pytest.mark.django_db()
class TestEmptyHumanMessageReplay:
    """Human messages already stored with empty content must not replay as empty text blocks.

    Anthropic rejects those with `400 text content blocks must contain non-whitespace text`,
    which broke every subsequent turn in a session that contained one.
    """

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param("", EMPTY_MESSAGE_PLACEHOLDER, id="empty"),
            pytest.param("   ", EMPTY_MESSAGE_PLACEHOLDER, id="spaces"),
            pytest.param("\n\t", EMPTY_MESSAGE_PLACEHOLDER, id="whitespace"),
            pytest.param("Hi", "Hi", id="normal-content-untouched"),
            pytest.param("  padded  ", "  padded  ", id="padding-preserved"),
        ],
    )
    def test_human_message_content(self, content, expected):
        session = ExperimentSessionFactory.create()
        message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content=content)
        assert message.to_langchain_message().content == expected

    def test_ai_message_is_not_substituted(self):
        """Empty AI messages are out of scope for this fix -- they are tracked separately."""
        session = ExperimentSessionFactory.create()
        message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="")
        assert message.to_langchain_message().content == ""

    def test_history_replay_substitutes_placeholder(self):
        session = ExperimentSessionFactory.create()
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="")
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="How can I help?")

        messages = session.chat.get_langchain_messages_until_marker(PipelineChatHistoryModes.SUMMARIZE)

        assert [m.content for m in messages] == [EMPTY_MESSAGE_PLACEHOLDER, "How can I help?"]
