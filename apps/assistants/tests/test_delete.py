import pytest

from apps.assistants.models import ToolResources
from apps.assistants.sync import _get_files_to_delete
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])


@pytest.fixture()
def code_resource(assistant):
    files = FileFactory.create_batch(2, team=assistant.team)

    tool_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=assistant)
    tool_resource.files.set(files)
    return tool_resource


@pytest.mark.django_db()
def test_files_to_delete_when_only_referenced_by_one_resource(code_resource):
    files_to_delete = list(_get_files_to_delete(code_resource.assistant.team, code_resource.id))
    assert len(files_to_delete) == 2
    assert {f.id for f in files_to_delete} == {f.id for f in code_resource.files.all()}


@pytest.mark.django_db()
def test_files_not_to_delete_when_referenced_by_multiple_resources(code_resource):
    all_files = list(code_resource.files.all())
    tool_resource = ToolResources.objects.create(tool_type="file_search", assistant=code_resource.assistant)
    tool_resource.files.set([all_files[0]])

    # only the second file should be deleted
    files_to_delete = list(_get_files_to_delete(code_resource.assistant.team, code_resource.id))
    assert len(files_to_delete) == 1
    assert files_to_delete[0].id == all_files[1].id

    files_to_delete = list(_get_files_to_delete(tool_resource.assistant.team, tool_resource.id))
    assert len(files_to_delete) == 0
