from io import BytesIO
from unittest.mock import patch

import pytest

from apps.assistants.sync import (
    delete_openai_assistant,
    import_openai_assistant,
    push_assistant_to_openai,
    sync_from_openai,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.openai import AssistantFactory, FileObjectFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.create", return_value=AssistantFactory.build(id="test_id"))
@patch("openai.resources.Files.create", side_effect=FileObjectFactory.create_batch(3))
def test_push_assistant_to_openai_create(mock_file_create, mock_create):
    local_assistant = OpenAiAssistantFactory()
    files = FileFactory.create_batch(3)
    local_assistant.files.set(files)
    push_assistant_to_openai(local_assistant)
    assert mock_create.called
    assert mock_file_create.call_count == 3
    local_assistant.refresh_from_db()
    assert local_assistant.assistant_id == "test_id"
    for file in files:
        file.refresh_from_db()
        assert file.external_id
        assert file.external_source == "openai"


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.update")
def test_push_assistant_to_openai_update(mock_update):
    local_assistant = OpenAiAssistantFactory(assistant_id="test_id")
    files = FileFactory.create_batch(3)
    local_assistant.files.set(files)
    files[0].external_id = "test_id"
    files[0].external_source = "openai"
    files[0].save()

    openai_files = FileObjectFactory.create_batch(2)
    with patch("openai.resources.Files.create", side_effect=openai_files) as mock_file_create:
        push_assistant_to_openai(local_assistant)
    assert mock_update.called
    assert mock_file_create.call_count == 2

    file_ids = {file.id for file in openai_files}
    for file in files[1:]:
        file.refresh_from_db()
        assert file.external_id in file_ids
        assert file.external_source == "openai"


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.retrieve")
@patch("openai.resources.Files.content", return_value=BytesIO(b"test_content"))
@patch("openai.resources.Files.retrieve")
def test_sync_from_openai(mock_file_retrieve, mock_file_content, mock_retrieve):
    openai_files = FileObjectFactory.create_batch(2)
    remote_assistant = AssistantFactory(file_ids=[file.id for file in openai_files])
    mock_retrieve.return_value = remote_assistant
    mock_file_retrieve.side_effect = openai_files

    # setup local assistant
    local_assistant = OpenAiAssistantFactory()
    files = FileFactory.create_batch(2)
    local_assistant.files.set(files)
    files[0].external_id = openai_files[0].id  # matches remote file
    files[1].external_id = "old_file"  # does not match remote file

    sync_from_openai(local_assistant)
    assert mock_retrieve.call_count == 1

    local_assistant.refresh_from_db()
    assert local_assistant.name == remote_assistant.name
    assert local_assistant.instructions == remote_assistant.instructions
    assert local_assistant.llm_model == remote_assistant.model
    assert local_assistant.builtin_tools == ["code_interpreter", "retrieval"]


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.retrieve")
@patch("openai.resources.Files.retrieve")
@patch("openai.resources.Files.content", return_value=BytesIO(b"test_content"))
def test_import_openai_assistant(mock_retrieve_content, mock_file_retrieve, mock_retrieve):
    openai_files = FileObjectFactory.create_batch(2)
    remote_assistant = AssistantFactory(file_ids=[file.id for file in openai_files])
    mock_retrieve.return_value = remote_assistant
    mock_file_retrieve.side_effect = openai_files
    llm_provider = LlmProviderFactory()
    imported_assistant = import_openai_assistant("123", llm_provider, llm_provider.team)
    assert imported_assistant.llm_provider == llm_provider
    assert imported_assistant.team == llm_provider.team
    assert imported_assistant.assistant_id == remote_assistant.id
    assert imported_assistant.name == remote_assistant.name
    assert imported_assistant.instructions == remote_assistant.instructions
    assert imported_assistant.llm_model == remote_assistant.model
    assert imported_assistant.builtin_tools == ["code_interpreter", "retrieval"]
    assert imported_assistant.files.count() == 2
    assert [(f.external_source, f.external_id) for f in imported_assistant.files.all()] == [
        ("openai", file.id) for file in openai_files
    ]


@pytest.mark.django_db()
@patch("openai.resources.beta.Assistants.delete")
@patch("openai.resources.beta.assistants.Files.delete")
@patch("openai.resources.Files.delete")
def test_delete_openai_assistant(mock_file_delete, mock_assistant_file_delete, mock_delete):
    files = FileFactory.create_batch(2, external_id="test_id", external_source="openai")
    local_assistant = OpenAiAssistantFactory()
    local_assistant.files.set(files)

    delete_openai_assistant(local_assistant)
    mock_delete.assert_called_with(local_assistant.assistant_id)
    assert mock_file_delete.call_count == 2
    assert mock_assistant_file_delete.call_count == 2
