from unittest.mock import patch

import pytest
from langchain_community.utilities.openapi import OpenAPISpec

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.tests.test_openapi_tool import _make_openapi_schema
from apps.ocs_notifications.models import LevelChoices
from apps.service_providers.auth_service import anonymous_auth_service
from apps.utils.factories.custom_actions import CustomActionFactory


def _test_tool_call_with_custom_action(spec_dict, call_args: dict, custom_action, path=None):
    """Helper function to test tool calls with custom action context for notifications."""
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    path = path or list(spec.paths)[0]
    function_def = openapi_spec_op_to_function_def(spec, path, "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service, custom_action=custom_action)
    return tool.run(call_args, tool_call_id="123")


@patch("apps.ocs_notifications.notifications.create_notification")
def test_openapi_tool_creates_error_notification_on_failure(mock_create_notification, httpx_mock):
    """Test that error notifications are created when API calls fail."""
    # Create a custom action using the factory (build to avoid DB persistence)
    custom_action = CustomActionFactory.build()

    spec = _make_openapi_schema({})
    # Mock a 500 error from the API
    httpx_mock.add_response(url="https://example.com/test", status_code=500, text="Internal Server Error")

    # ToolException is handled by StructuredTool (handle_tool_error=True),
    # so it returns the error string instead of raising.
    result = _test_tool_call_with_custom_action(spec, {}, custom_action)
    assert "Error making request" in str(result)

    # Verify the error notification was created
    mock_create_notification.assert_called_once()
    call_args = mock_create_notification.call_args

    assert call_args[1]["title"] == f"Custom Action '{custom_action.name}' failed"
    assert "API call failed" in call_args[1]["message"]
    assert call_args[1]["level"] == LevelChoices.ERROR
    assert call_args[1]["team"] == custom_action.team
    assert call_args[1]["slug"] == "custom-action-api-failure"
    assert call_args[1]["event_data"]["action_id"] == custom_action.id
    assert call_args[1]["event_data"]["exception_type"] == "ToolException"


@patch("apps.ocs_notifications.notifications.create_notification")
def test_openapi_tool_no_notifications_without_custom_action(mock_create_notification, httpx_mock):
    """Test that no notifications are created when custom_action is not provided."""
    spec = _make_openapi_schema({})
    httpx_mock.add_response(url="https://example.com/test", text="Success")

    # Use the original helper function without custom_action
    spec_obj = OpenAPISpec.from_spec_dict(spec)
    path = list(spec_obj.paths)[0]
    function_def = openapi_spec_op_to_function_def(spec_obj, path, "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service)  # No custom_action
    tool.run({}, tool_call_id="123")

    # Verify no notifications were created
    mock_create_notification.assert_not_called()


@patch("apps.ocs_notifications.notifications.create_notification")
def test_openapi_tool_creates_unexpected_error_notification(mock_create_notification):
    """Test that error notifications are created for unexpected exceptions."""
    custom_action = CustomActionFactory.build()

    spec = _make_openapi_schema({})

    # Mock the original call_api to raise an unexpected exception
    with patch("apps.chat.agent.openapi_tool.OpenAPIOperationExecutor.call_api") as mock_call_api:
        mock_call_api.side_effect = ConnectionError("Connection Error")

        with pytest.raises(ConnectionError):
            _test_tool_call_with_custom_action(spec, {}, custom_action)

    # Verify the unexpected error notification was created
    mock_create_notification.assert_called_once()
    call_args = mock_create_notification.call_args

    assert call_args[1]["title"] == f"Custom Action '{custom_action.name}' encountered an error"
    assert call_args[1]["message"] == "GET 'test_get' failed with an unexpected error: Connection Error"
    assert call_args[1]["level"] == LevelChoices.ERROR
    assert call_args[1]["team"] == custom_action.team
    assert call_args[1]["slug"] == "custom-action-unexpected-error"
    assert call_args[1]["event_data"]["action_id"] == custom_action.id
    assert call_args[1]["event_data"]["exception_type"] == "ConnectionError"
