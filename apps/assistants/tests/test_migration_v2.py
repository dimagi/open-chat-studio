import pytest

from apps.assistants.migrations.utils import migrate_assistant_to_v2
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_migrate_assistant():
    assistant = OpenAiAssistantFactory(builtin_tools=["retrieval", "code_interpreter"])
    files = FileFactory.create_batch(2, external_id="test_id", external_source="openai")
    assistant.files.set(files)

    migrate_assistant_to_v2(assistant)

    assert set(assistant.builtin_tools) == {"file_search", "code_interpreter"}
    assert assistant.files.count() == 0
    assert assistant.tool_resources.count() == 2

    file_search = assistant.tool_resources.filter(tool_type="file_search").first()
    assert file_search.tool_type == "file_search"
    assert file_search.files.count() == 2
    assert file_search.extra == {}

    code_interpreter = assistant.tool_resources.filter(tool_type="code_interpreter").first()
    assert code_interpreter.tool_type == "code_interpreter"
    assert code_interpreter.files.count() == 2
