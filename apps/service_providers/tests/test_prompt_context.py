from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError

from apps.channels.models import ChannelPlatform
from apps.experiments.models import SourceMaterial
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.utils.prompt import validate_prompt_variables


@pytest.fixture()
def mock_session():
    session = Mock()
    session.experiment_channel.platform = ChannelPlatform.WEB
    session.participant.user = None

    proxy_mock = Mock()
    proxy_mock.get.return_value = "participant data"
    proxy_mock.get_timezone.return_value = "UTC"
    session._proxy_mock = proxy_mock
    with patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy", return_value=proxy_mock):
        yield session


@pytest.fixture()
def mock_authorized_session(mock_session):
    mock_session.participant.user = Mock()
    return mock_session


@patch("apps.experiments.models.SourceMaterial.objects.get")
def test_builds_context_with_specified_variables(mock_get, mock_session):
    mock_get.return_value = Mock(material="source material")
    proxy_mock = mock_session._proxy_mock
    context = PromptTemplateContext(mock_session, 1)
    variables = ["source_material", "current_datetime"]
    result = context.get_context(variables)
    assert "source_material" in result
    assert "current_datetime" in result
    assert "participant_data" not in result

    proxy_mock.get.assert_not_called()


def test_repeated_calls_are_cached(mock_authorized_session):
    proxy_mock = mock_authorized_session._proxy_mock
    context = PromptTemplateContext(mock_authorized_session, 1)
    result = context.get_context([])
    assert result == {}

    proxy_mock.get.assert_not_called()

    result = context.get_context(["participant_data"])
    assert result == {"participant_data": "participant data"}
    proxy_mock.get.assert_called_once()

    proxy_mock.get.reset_mock()
    result = context.get_context(["participant_data"])
    assert result == {"participant_data": "participant data"}

    proxy_mock.get.assert_not_called()


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


def test_retrieves_participant_data_when_authorized(mock_authorized_session):
    context = PromptTemplateContext(mock_authorized_session, 1)
    assert context.is_unauthorized_participant is False
    assert context.get_participant_data() == "participant data"


def test_returns_empty_string_when_unauthorized_participant(mock_session):
    context = PromptTemplateContext(mock_session, 1)
    assert context.is_unauthorized_participant is True
    assert context.get_participant_data() == ""


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
