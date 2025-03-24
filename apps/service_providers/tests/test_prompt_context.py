from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ChannelPlatform
from apps.documents.models import Repository
from apps.experiments.models import SourceMaterial
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def mock_session():
    session = Mock()
    session.get_participant_data.return_value = "participant data"
    session.get_participant_timezone.return_value = "UTC"
    session.experiment_channel.platform = ChannelPlatform.WEB
    session.participant.user = None
    return session


@pytest.fixture()
def mock_authorized_session(mock_session):
    mock_session.participant.user = Mock()
    return mock_session


@patch("apps.experiments.models.SourceMaterial.objects.get")
def test_builds_context_with_specified_variables(mock_get, mock_session):
    mock_get.return_value = Mock(material="source material")
    context = PromptTemplateContext(mock_session, 1)
    variables = ["source_material", "current_datetime"]
    result = context.get_context(variables)
    assert "source_material" in result
    assert "current_datetime" in result
    assert "participant_data" not in result
    mock_session.get_participant_data.assert_not_called()


def test_repeated_calls_are_cached(mock_authorized_session):
    context = PromptTemplateContext(mock_authorized_session, 1)
    result = context.get_context([])
    assert result == {}
    mock_authorized_session.get_participant_data.assert_not_called()

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": "participant data"}
    mock_authorized_session.get_participant_data.assert_called_once()

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": "participant data"}
    mock_authorized_session.get_participant_data.assert_called_once()


def test_calls_with_different_vars_returns_correct_context(mock_session):
    context = PromptTemplateContext(mock_session, 1)
    result = context.get_context(["current_datetime"])
    assert "current_datetime" in result

    result = context.get_context([])
    assert result == {}

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": ""}


@patch("apps.experiments.models.SourceMaterial.objects.get")
def test_retrieves_source_material_successfully(mock_get, mock_session):
    mock_get.return_value = Mock(material="source material")
    context = PromptTemplateContext(mock_session, 1)
    assert context.get_source_material() == "source material"


@patch("apps.experiments.models.SourceMaterial.objects.get")
def test_returns_blank_source_material_not_found(mock_get, mock_session):
    mock_get.side_effect = SourceMaterial.DoesNotExist
    context = PromptTemplateContext(mock_session, 1)
    assert context.get_source_material() == ""


@pytest.mark.django_db()
def test_retrieves_media_successfully():
    collection = CollectionFactory()
    file1 = FileFactory(summary="summary1", team_id=collection.team_id)
    file2 = FileFactory(summary="summary2", team_id=collection.team_id)
    collection.files.add(file1, file2)
    context = PromptTemplateContext(session=None, source_material_id=None, collection_id=collection.id)
    expected_media_summaries = (
        f"* File (id={file1.id}, content_type={file1.content_type}): {file1.summary}\n\n"
        f"* File (id={file2.id}, content_type={file2.content_type}): {file2.summary}\n"
    )
    assert context.get_media_summaries() == expected_media_summaries


@patch("apps.documents.models.Repository.objects.collections")
def test_returns_blank_when_collection_not_found(collections_mock):
    collections_mock.side_effect = Repository.DoesNotExist
    context = PromptTemplateContext(session=None, source_material_id=1, collection_id=999)
    assert context.get_media_summaries() == ""


def test_retrieves_participant_data_when_authorized(mock_authorized_session):
    context = PromptTemplateContext(mock_authorized_session, 1)
    assert context.is_unauthorized_participant is False
    assert context.get_participant_data() == "participant data"


def test_returns_empty_string_when_unauthorized_participant(mock_session):
    context = PromptTemplateContext(mock_session, 1)
    assert context.is_unauthorized_participant is True
    assert context.get_participant_data() == ""
