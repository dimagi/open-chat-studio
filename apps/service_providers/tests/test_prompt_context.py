from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError

from apps.channels.models import ChannelPlatform
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.prompt import validate_prompt_variables


@pytest.fixture()
def mock_repo():
    repo = Mock()
    repo.get_source_material.return_value = None
    repo.get_collection.return_value = None
    repo.get_collection_index_summaries.return_value = ""
    return repo


@pytest.fixture()
def mock_participant_data_proxy():
    proxy_mock = Mock()
    proxy_mock.get.return_value = {"name": "Dimagi", "email": "hello@world.com"}
    proxy_mock.get_timezone.return_value = "UTC"
    proxy_mock.get_schedules.return_value = []
    with patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy", return_value=proxy_mock):
        yield proxy_mock


@pytest.fixture()
def mock_session(mock_participant_data_proxy):
    session = Mock()
    session.experiment_channel.platform = ChannelPlatform.WEB
    session.participant.user = None
    return session


def test_builds_context_with_specified_variables(mock_session, mock_participant_data_proxy, mock_repo):
    mock_repo.get_source_material.return_value = Mock(material="source material")
    context = PromptTemplateContext(mock_session, 1, repo=mock_repo)
    variables = ["source_material", "current_datetime"]
    result = context.get_context(variables)
    assert "source_material" in result
    assert "current_datetime" in result
    assert "participant_data" not in result

    mock_participant_data_proxy.get.assert_not_called()


def test_repeated_calls_are_cached(mock_session, mock_participant_data_proxy, mock_repo):
    context = PromptTemplateContext(mock_session, 1, repo=mock_repo)
    result = context.get_context([])
    assert result == {}

    mock_participant_data_proxy.get.assert_not_called()

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": {"name": "Dimagi", "email": "hello@world.com"}}
    mock_participant_data_proxy.get.assert_called_once()

    mock_participant_data_proxy.get.reset_mock()
    result = context.get_context(["participant_data"])
    assert result == {"participant_data": {"name": "Dimagi", "email": "hello@world.com"}}

    mock_participant_data_proxy.get.assert_not_called()


def test_calls_with_different_vars_returns_correct_context(mock_session, mock_repo):
    context = PromptTemplateContext(mock_session, 1, repo=mock_repo)
    result = context.get_context(["current_datetime"])
    assert "current_datetime" in result

    result = context.get_context([])
    assert result == {}

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": {"name": "Dimagi", "email": "hello@world.com"}}


def test_retrieves_source_material_successfully(mock_session, mock_repo):
    mock_repo.get_source_material.return_value = Mock(material="source material")
    context = PromptTemplateContext(mock_session, 1, repo=mock_repo)
    assert context.get_source_material() == "source material"


def test_returns_blank_source_material_not_found(mock_session, mock_repo):
    mock_repo.get_source_material.return_value = None
    context = PromptTemplateContext(mock_session, 1, repo=mock_repo)
    assert context.get_source_material() == ""


@pytest.mark.django_db()
def test_retrieves_media_successfully(mock_session, mock_repo):
    collection = CollectionFactory()
    file1 = FileFactory(summary="summary1", team_id=collection.team_id)
    file2 = FileFactory(summary="summary2", team_id=collection.team_id)
    collection.files.add(file1, file2)
    mock_repo.get_collection.return_value = collection
    context = PromptTemplateContext(session=mock_session, collection_id=collection.id, repo=mock_repo)
    expected_media_summaries = [
        f"* File (id={file1.id}, content_type={file1.content_type}): {file1.summary}\n",
        f"* File (id={file2.id}, content_type={file2.content_type}): {file2.summary}\n",
    ]
    summaries = context.get_media_summaries()
    assert expected_media_summaries[0] in summaries
    assert expected_media_summaries[1] in summaries


def test_returns_blank_when_collection_not_found(mock_session, mock_repo):
    mock_repo.get_collection.return_value = None
    context = PromptTemplateContext(session=mock_session, source_material_id=1, collection_id=999, repo=mock_repo)
    assert context.get_media_summaries() == ""


def test_retrieves_participant_data_when_authorized(mock_session, mock_repo):
    context = PromptTemplateContext(mock_session, repo=mock_repo)
    assert context.get_participant_data() == {"name": "Dimagi", "email": "hello@world.com"}


def test_participant_data_includes_schedules(mock_session, mock_participant_data_proxy, mock_repo):
    mock_participant_data_proxy.get_schedules.return_value = [{"id": 1}]
    context = PromptTemplateContext(mock_session, repo=mock_repo)
    assert context.get_participant_data() == {
        "name": "Dimagi",
        "email": "hello@world.com",
        "scheduled_messages": [{"id": 1}],
    }


def test_invalid_format_specifier_not_caught():
    """
    Test that invalid format specifiers are caught with ValidationError (Sentry OPEN-CHAT-STUDIO-R1).
    """
    form_data = {"prompt": "{source_material:abcd}", "source_material": "some text"}
    prompt_key = "prompt"
    known_vars = {"source_material"}

    with pytest.raises(ValidationError, match="Invalid prompt variable '{source_material:abcd}'. Remove the ':abcd'."):
        validate_prompt_variables(form_data, prompt_key, known_vars)


def test_invalid_conversion_caught():
    """
    Test that conversion specifiers (e.g., !s) are caught with ValidationError.
    """
    form_data = {"prompt": "{var!s}", "source_material": "some text"}
    prompt_key = "prompt"
    known_vars = {"var"}

    with pytest.raises(ValidationError, match="Invalid prompt variable '{var!s}'. Remove the '!s'."):
        validate_prompt_variables(form_data, prompt_key, known_vars)


def test_invalid_conversion_and_specifier_caught():
    """
    Test that both conversion and format specifiers (e.g., !r:xyz) are caught with ValidationError.
    """
    form_data = {"prompt": "{var!r:xyz}", "source_material": "some text"}
    prompt_key = "prompt"
    known_vars = {"var"}

    with pytest.raises(ValidationError, match="Invalid prompt variable '{var!r:xyz}'. Remove the '!r:xyz'."):
        validate_prompt_variables(form_data, prompt_key, known_vars)


def test_extra_context_is_included(mock_session, mock_repo):
    extra_context = {"custom_var": "custom_value"}
    context = PromptTemplateContext(mock_session, extra=extra_context, repo=mock_repo)
    result = context.get_context(["custom_var"])
    assert result == {"custom_var": "custom_value"}

    # Ensure other context variables are still available
    result = context.get_context(["participant_data"])
    assert "participant_data" in result
