import uuid
from unittest.mock import Mock

import pytest

from apps.assistants.models import ToolResources
from apps.assistants.sync import _get_files_to_delete, delete_openai_files_for_resource
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])


@pytest.fixture()
def code_resource(assistant):
    files = FileFactory.create_batch(2, team=assistant.team)
    for f in files:
        f.external_id = str(uuid.uuid4())
        f.external_source = "openai"
        f.save()

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


@pytest.mark.django_db()
def test_delete_openai_files_for_resource(code_resource):
    all_files = list(code_resource.files.all())
    assert all(f.external_id for f in all_files)
    assert all(f.external_source for f in all_files)
    client = Mock()
    delete_openai_files_for_resource(client, code_resource.assistant.team, code_resource)

    assert client.files.delete.call_count == 2
    all_files = list(code_resource.files.all())
    assert not any(f.external_id for f in all_files)
    assert not any(f.external_source for f in all_files)
