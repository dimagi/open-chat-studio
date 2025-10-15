import uuid

import pytest

from apps.assistants.models import ToolResources
from apps.files.models import File
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])


@pytest.fixture()
def code_resource(assistant):
    files = FileFactory.create_batch(3, team=assistant.team)
    for f in files:
        f.external_id = str(uuid.uuid4())
        f.external_source = "openai"
        f.save()

    tool_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=assistant)
    tool_resource.files.set(files)
    return tool_resource, files


@pytest.mark.django_db()
def test_deleting_tool_resource_with_files(code_resource):
    resource, files = code_resource

    resource.delete()

    assert File.objects.count() == 0


@pytest.mark.django_db()
def test_deleting_tool_resource_with_shared_files(code_resource, assistant, caplog):
    resource1, files = code_resource

    resource2 = ToolResources.objects.create(tool_type="file_search", assistant=assistant)
    resource2.files.set([files[0]])

    resource1.delete()

    remaining = File.objects.all()
    assert remaining.count() == 1
    assert remaining[0] == files[0]

    assert str(files[0].id) in caplog.text
