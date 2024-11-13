import dataclasses
from io import BytesIO
from unittest.mock import call, patch

import pytest
from openai.pagination import SyncCursorPage

from apps.assistants.models import ToolResources
from apps.assistants.sync import (
    are_files_in_sync_with_openai,
    delete_openai_assistant,
    import_openai_assistant,
    push_assistant_to_openai,
    sync_from_openai,
)
from apps.chat.agent import tools
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.openai import AssistantFactory, FileObjectFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@dataclasses.dataclass
class ObjectWithId:
    id: str


@pytest.mark.django_db()
@patch("openai.resources.beta.vector_stores.VectorStores.create", return_value=ObjectWithId(id="vs_123"))
@patch("openai.resources.beta.Assistants.create", return_value=AssistantFactory.build(id="test_id"))
@patch("openai.resources.Files.create", side_effect=FileObjectFactory.create_batch(3))
def test_push_assistant_to_openai_create(mock_file_create, assistant_create, vs_create):
    local_assistant = OpenAiAssistantFactory(builtin_tools=["code_interpreter", "file_search"])
    files = FileFactory.create_batch(3)

    code_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=local_assistant)
    code_resource.files.set(files[:2])

    search_resource = ToolResources.objects.create(tool_type="file_search", assistant=local_assistant)
    search_resource.files.set(files[2:])

    push_assistant_to_openai(local_assistant)
    assert assistant_create.called
    assert mock_file_create.call_count == 3
    assert vs_create.called
    local_assistant.refresh_from_db()
    assert local_assistant.assistant_id == "test_id"
    for file in files:
        file.refresh_from_db()
        assert file.external_id
        assert file.external_source == "openai"

    search_resource.refresh_from_db()
    assert search_resource.extra == {"vector_store_id": "vs_123"}


@pytest.mark.django_db()
@patch("openai.resources.beta.vector_stores.file_batches.FileBatches.create")
@patch("openai.resources.beta.vector_stores.files.Files.list")
@patch("openai.resources.beta.vector_stores.VectorStores.retrieve", return_value=ObjectWithId(id="vs_123"))
@patch("openai.resources.beta.Assistants.update")
def test_push_assistant_to_openai_update(mock_update, vs_retrieve, vs_files_list, file_batches, experiment):
    local_assistant = OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])
    files = FileFactory.create_batch(3)
    files[0].external_id = "test_id"
    files[0].external_source = "openai"
    files[0].save()

    code_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=local_assistant)
    code_resource.files.set(files[:2])

    search_resource = ToolResources.objects.create(
        tool_type="file_search", assistant=local_assistant, extra={"vector_store_id": "vs_123"}
    )
    search_resource.files.set(files[2:])

    vs_files_list.return_value = SyncCursorPage(
        data=[],
        object="list",
        first_id=None,
        last_id=None,
        has_more=False,
    )

    openai_files = FileObjectFactory.create_batch(2)

    internal_tools = [tool_cls(experiment_session=None) for tool_cls in tools.TOOL_CLASS_MAP.values()]
    with patch("openai.resources.Files.create", side_effect=openai_files) as mock_file_create:
        push_assistant_to_openai(local_assistant, internal_tools=internal_tools)
    assert mock_update.called
    assert vs_retrieve.called
    assert mock_file_create.call_count == 2

    # Make sure that all tools in TOOL_CLASS_MAP was speceified
    tool_specs = mock_update.call_args_list[0].kwargs.get("tools")
    tool_names = set([tool_spec["function"]["name"] for tool_spec in tool_specs])
    expected_tools = set(tools.TOOL_CLASS_MAP.keys())
    assert expected_tools - tool_names == set()

    assert file_batches.call_args_list == [
        call(vector_store_id="vs_123", file_ids=[openai_files[-1].id]),
    ]

    file_ids = {file.id for file in openai_files}
    for file in files[1:]:
        file.refresh_from_db()
        assert file.external_id in file_ids
        assert file.external_source == "openai"


@pytest.mark.django_db()
@patch("openai.resources.beta.vector_stores.files.Files.list")
@patch("openai.resources.beta.Assistants.retrieve")
@patch("openai.resources.Files.content", return_value=BytesIO(b"test_content"))
@patch("openai.resources.Files.retrieve")
def test_sync_from_openai(mock_file_retrieve, _, mock_retrieve, mock_vector_store_files):
    openai_files = FileObjectFactory.create_batch(4)
    code_files_expected = openai_files[:2]
    file_search_files_expected = openai_files[2:]

    # mock assistant return value
    remote_assistant = AssistantFactory()
    remote_assistant.tool_resources.code_interpreter.file_ids = [file.id for file in code_files_expected]
    vector_store_id = "vs_123"
    remote_assistant.tool_resources.file_search.vector_store_ids = [vector_store_id]

    # mock the assistant api call
    mock_retrieve.return_value = remote_assistant

    # this will return one file from the list on each call to the mock
    mock_file_retrieve.side_effect = openai_files

    # mock the vector store file call
    mock_vector_store_files.return_value = [FileObjectFactory(id=file.id) for file in file_search_files_expected]

    # setup local assistant
    files = FileFactory.create_batch(2)
    files[0].external_id = openai_files[0].id  # matches remote file
    files[1].external_id = "old_file"  # does not match remote file
    [file.save() for file in files]

    local_assistant = OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])
    code_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=local_assistant)
    code_resource.files.set([files[0]])

    search_resource = ToolResources.objects.create(
        tool_type="file_search", assistant=local_assistant, extra={"vector_store_id": "vs_123"}
    )
    search_resource.files.set([files[1]])

    sync_from_openai(local_assistant)
    assert mock_retrieve.call_count == 1

    local_assistant.refresh_from_db()
    assert local_assistant.name == remote_assistant.name
    assert local_assistant.instructions == remote_assistant.instructions
    assert local_assistant.llm_provider_model.name == remote_assistant.model
    assert local_assistant.temperature == remote_assistant.temperature
    assert local_assistant.top_p == remote_assistant.top_p
    assert local_assistant.builtin_tools == ["code_interpreter", "file_search"]

    code_resource.refresh_from_db()
    assert code_resource.files.count() == 2

    search_resource.refresh_from_db()
    assert search_resource.extra["vector_store_id"] == vector_store_id
    assert search_resource.files.count() == 2


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.retrieve")
@patch("openai.resources.beta.vector_stores.files.Files.list")
@patch("openai.resources.Files.retrieve")
@patch("openai.resources.Files.content", return_value=BytesIO(b"test_content"))
def test_import_openai_assistant(_, mock_file_retrieve, mock_vector_store_files, mock_retrieve):
    openai_files = FileObjectFactory.create_batch(4)
    code_files_expected = openai_files[:2]
    file_search_files_expected = openai_files[2:]

    # mock assistant return value
    remote_assistant = AssistantFactory()
    remote_assistant.tool_resources.code_interpreter.file_ids = [file.id for file in code_files_expected]
    vector_store_id = "vs_123"
    remote_assistant.tool_resources.file_search.vector_store_ids = [vector_store_id]

    # mock the assistant apo call
    mock_retrieve.return_value = remote_assistant

    # mock the vector store file call
    mock_vector_store_files.return_value = [FileObjectFactory(id=file.id) for file in file_search_files_expected]

    # this will return one file from the list on each call to the mock
    mock_file_retrieve.side_effect = openai_files

    llm_provider = LlmProviderFactory()
    imported_assistant = import_openai_assistant("123", llm_provider, llm_provider.team)
    assert imported_assistant.llm_provider == llm_provider
    assert imported_assistant.team == llm_provider.team
    assert imported_assistant.assistant_id == remote_assistant.id
    assert imported_assistant.name == remote_assistant.name
    assert imported_assistant.instructions == remote_assistant.instructions
    assert imported_assistant.llm_provider_model.name == remote_assistant.model
    assert imported_assistant.temperature == remote_assistant.temperature
    assert imported_assistant.top_p == remote_assistant.top_p
    assert imported_assistant.builtin_tools == ["code_interpreter", "file_search"]
    assert imported_assistant.files.count() == 0
    assert imported_assistant.tool_resources.count() == 2
    code_files = imported_assistant.tool_resources.filter(tool_type="code_interpreter").first().files.all()
    assert [(f.external_source, f.external_id) for f in code_files] == [
        ("openai", file.id) for file in code_files_expected
    ]
    file_search_resource = imported_assistant.tool_resources.filter(tool_type="file_search").first()
    assert file_search_resource.extra["vector_store_id"] == vector_store_id

    file_search_files = file_search_resource.files.all()
    assert [(f.external_source, f.external_id) for f in file_search_files] == [
        ("openai", file.id) for file in file_search_files_expected
    ]


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.delete")
@patch("openai.resources.beta.vector_stores.VectorStores.delete")
@patch("openai.resources.Files.delete")
def test_delete_openai_assistant(mock_file_delete, mock_vector_store_delete, mock_delete):
    files = FileFactory.create_batch(3, external_id="test_id", external_source="openai")
    local_assistant = OpenAiAssistantFactory()

    code_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=local_assistant)
    code_resource.files.set(files[:2])

    search_resource = ToolResources.objects.create(
        tool_type="file_search", assistant=local_assistant, extra={"vector_store_id": "vs_123"}
    )
    search_resource.files.set(files[2:])

    delete_openai_assistant(local_assistant)
    mock_delete.assert_called_with(local_assistant.assistant_id)
    assert mock_file_delete.call_count == 3
    assert mock_vector_store_delete.call_count == 1


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.retrieve")
def test_code_interpreter_are_files_in_sync_with_openai(mock_retrieve):
    tool_type = "code_interpreter"
    openai_files = FileObjectFactory.create_batch(2)

    remote_assistant = AssistantFactory()
    remote_assistant.tool_resources.code_interpreter.file_ids = [file.id for file in openai_files]
    del remote_assistant.tool_resources.file_search
    mock_retrieve.return_value = remote_assistant

    # setup local assistant
    files = FileFactory.create_batch(2)
    files[0].external_id = openai_files[0].id
    files[1].external_id = openai_files[1].id
    [file.save() for file in files]

    local_assistant = OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=[tool_type])
    resource = ToolResources.objects.create(tool_type=tool_type, assistant=local_assistant)
    resource.files.set([files[0]])

    assert are_files_in_sync_with_openai(local_assistant) is False

    # Update local files to match remote files
    resource.files.set(files)
    assert are_files_in_sync_with_openai(local_assistant) is True


@pytest.mark.django_db()
@patch("openai.resources.beta.vector_stores.files.Files.list")
@patch("openai.resources.beta.Assistants.retrieve")
def test_file_search_are_files_in_sync_with_openai(mock_retrieve, file_list):
    tool_type = "file_search"
    openai_files = FileObjectFactory.create_batch(2)
    file_list.return_value = [FileObjectFactory(id=file.id) for file in openai_files]

    remote_assistant = AssistantFactory()
    vector_store_id = "vs_123"
    remote_assistant.tool_resources.file_search.vector_store_ids = [vector_store_id]
    del remote_assistant.tool_resources.code_interpreter
    mock_retrieve.return_value = remote_assistant

    # setup local assistant
    files = FileFactory.create_batch(2)
    files[0].external_id = openai_files[0].id
    files[1].external_id = openai_files[1].id
    [file.save() for file in files]

    local_assistant = OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=[tool_type])
    resource = ToolResources.objects.create(
        tool_type="file_search", assistant=local_assistant, extra={"vector_store_id": vector_store_id}
    )
    # Test out of sync
    resource.files.set([files[0]])
    assert are_files_in_sync_with_openai(local_assistant) is False

    # Test in sync
    resource.files.set(files)
    assert are_files_in_sync_with_openai(local_assistant) is True
