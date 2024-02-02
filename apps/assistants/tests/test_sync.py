from unittest.mock import patch

import pytest

from apps.assistants.sync import (
    delete_openai_assistant,
    import_openai_assistant,
    push_assistant_to_openai,
    sync_from_openai,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.openai import AssistantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.mark.django_db
@patch("openai.resources.beta.Assistants.create", return_value=AssistantFactory.build(id="test_id"))
def test_push_assistant_to_openai_create(mock_create):
    local_assistant = OpenAiAssistantFactory()
    push_assistant_to_openai(local_assistant)
    assert mock_create.called
    local_assistant.refresh_from_db()
    assert local_assistant.assistant_id == "test_id"


@pytest.mark.django_db
@patch("openai.resources.beta.Assistants.update")
def test_push_assistant_to_openai_update(mock_update):
    local_assistant = OpenAiAssistantFactory(assistant_id="test_id")
    push_assistant_to_openai(local_assistant)
    assert mock_update.called


@pytest.mark.django_db
@patch("openai.resources.beta.Assistants.retrieve")
def test_sync_from_openai(mock_retrieve):
    remote_assistant = AssistantFactory()
    mock_retrieve.return_value = remote_assistant
    local_assistant = OpenAiAssistantFactory()
    sync_from_openai(local_assistant)
    local_assistant.refresh_from_db()
    assert local_assistant.name == remote_assistant.name
    assert local_assistant.instructions == remote_assistant.instructions
    assert local_assistant.llm_model == remote_assistant.model
    assert local_assistant.builtin_tools == ["code_interpreter", "retrieval"]


@pytest.mark.django_db
@patch("openai.resources.beta.Assistants.retrieve")
def test_import_openai_assistant(mock_retrieve):
    remote_assistant = AssistantFactory()
    mock_retrieve.return_value = remote_assistant
    llm_provider = LlmProviderFactory()
    imported_assistant = import_openai_assistant("123", llm_provider, llm_provider.team)
    assert imported_assistant.llm_provider == llm_provider
    assert imported_assistant.team == llm_provider.team
    assert imported_assistant.assistant_id == remote_assistant.id
    assert imported_assistant.name == remote_assistant.name
    assert imported_assistant.instructions == remote_assistant.instructions
    assert imported_assistant.llm_model == remote_assistant.model
    assert imported_assistant.builtin_tools == ["code_interpreter", "retrieval"]


@pytest.mark.django_db
@patch("openai.resources.beta.Assistants.delete")
def test_delete_openai_assistant(mock_delete):
    local_assistant = OpenAiAssistantFactory()
    delete_openai_assistant(local_assistant)
    mock_delete.assert_called_with(local_assistant.assistant_id)
