import pytest

from apps.assistants.models import ToolResources
from apps.files.models import File
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_deleting_assistant_with_files():
    assistant = OpenAiAssistantFactory()
    files = FileFactory.create_batch(3)
    ToolResources.objects.create(assistant=assistant, tool_type="code_interpreter").files.set(files)

    assistant.delete()

    assert File.objects.count() == 0


@pytest.mark.django_db()
def test_deleting_assistant_with_files_multiple_references(caplog):
    assistant = OpenAiAssistantFactory()
    files = FileFactory.create_batch(3)
    ToolResources.objects.create(assistant=assistant, tool_type="code_interpreter").files.set(files)

    assistant2 = OpenAiAssistantFactory()
    ToolResources.objects.create(assistant=assistant2, tool_type="code_interpreter").files.set(files[:1])

    assistant.delete()

    remaining = File.objects.all()
    assert len(remaining) == 1
    assert remaining[0] == files[0]
    assert str(files[0].id) in caplog.text
