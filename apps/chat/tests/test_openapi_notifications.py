from unittest.mock import patch

import pytest
from langchain_community.utilities.openapi import OpenAPISpec

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.tests.test_openapi_tool import _make_openapi_schema
from apps.service_providers.auth_service import anonymous_auth_service
from apps.utils.factories.custom_actions import CustomActionFactory


def _test_tool_call_with_custom_action(spec_dict, call_args: dict, custom_action, path=None):
    """Helper function to test tool calls with custom action context for notifications."""
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    path = path or list(spec.paths)[0]  # ty: ignore[invalid-argument-type]
    function_def = openapi_spec_op_to_function_def(spec, path, "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service, custom_action=custom_action)
    return tool.run(call_args, tool_call_id="123")


@patch("apps.custom_actions.models.get_slug_for_team")
@patch("apps.chat.agent.openapi_tool.custom_action_api_failure_notification")
def test_openapi_tool_creates_error_notification_on_failure(mock_create_notification, get_slug_for_team, httpx_mock):
    """Test that error notifications are created when API calls fail."""
    # Create a custom action using the factory (build to avoid DB persistence)
    get_slug_for_team.return_value = "test-team-slug"
    custom_action = CustomActionFactory.build(id=1)

    spec = _make_openapi_schema({})
    # Mock a 500 error from the API
    httpx_mock.add_response(url="https://example.com/test", status_code=500, text="Internal Server Error")

    # ToolException is handled by StructuredTool (handle_tool_error=True),
    # so it returns the error string instead of raising.
    result = _test_tool_call_with_custom_action(spec, {}, custom_action)
    assert "Error making request" in str(result)

    # Verify the error notification was created
    mock_create_notification.assert_called_once()


@patch("apps.custom_actions.models.get_slug_for_team")
@patch("apps.chat.agent.openapi_tool.custom_action_unexpected_error_notification")
def test_openapi_tool_creates_unexpected_error_notification(mock_create_notification, get_slug_for_team):
    """Test that error notifications are created for unexpected exceptions."""
    get_slug_for_team.return_value = "test-team-slug"
    custom_action = CustomActionFactory.build(id=1)

    spec = _make_openapi_schema({})

    # Mock the original call_api to raise an unexpected exception
    with patch("apps.chat.agent.openapi_tool.OpenAPIOperationExecutor.call_api") as mock_call_api:
        mock_call_api.side_effect = ConnectionError("Connection Error")

        with pytest.raises(ConnectionError):
            _test_tool_call_with_custom_action(spec, {}, custom_action)

    # Verify the unexpected error notification was created
    mock_create_notification.assert_called_once()
