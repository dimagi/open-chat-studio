import pytest

from apps.chat.models import ChatMessage
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_get_attached_files():
    """asdasd"""
    session = ExperimentSessionFactory(experiment__assistant=OpenAiAssistantFactory())
    assistant_file1 = FileFactory(external_id="assistant-file-id-1", team=session.chat.team)
    assistant_file2 = FileFactory(external_id="assistant-file-id-2", team=session.chat.team)
    assistant = session.experiment.assistant
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
    assert chat_file1 in files
    assert assistant_file1 not in files
    assert assistant_file2 not in files
