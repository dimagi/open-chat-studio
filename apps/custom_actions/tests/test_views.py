"""Tests for custom_actions view utilities."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.urls import reverse
from waffle.testutils import override_flag

from apps.custom_actions.schema_utils import APIOperationDetails
from apps.custom_actions.views import _call_action_operation
from apps.teams.flags import Flags
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.service_provider_factories import AuthProviderFactory


def make_operation(method="get", path="/items", operation_id="listItems"):
    return APIOperationDetails(
        operation_id=operation_id,
        description="Test operation",
        path=path,
        method=method,
    )


def make_response(status_code=200, json_data=None, text=None):
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("not json")
        mock.text = text or ""
    return mock


class TestCallActionOperation:
    @pytest.mark.parametrize("method", ["get", "delete"])
    def test_get_and_delete_send_params_as_query_string(self, method):
        op = make_operation(method=method)
        params = {"foo": "bar"}
        mock_resp = make_response(json_data={"ok": True})

        with patch(f"apps.custom_actions.views.httpx.{method}") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation("https://api.example.com", op, params, {}, {})

        mock_http.assert_called_once()
        _, kwargs = mock_http.call_args
        assert kwargs["params"] == params
        assert "json" not in kwargs

    @pytest.mark.parametrize("method", ["post", "put", "patch"])
    def test_post_put_patch_send_params_as_json_body(self, method):
        op = make_operation(method=method)
        params = {"name": "Alice"}
        mock_resp = make_response(json_data={"id": 1})

        with patch(f"apps.custom_actions.views.httpx.{method}") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation("https://api.example.com", op, params, {}, {})

        mock_http.assert_called_once()
        _, kwargs = mock_http.call_args
        assert kwargs["json"] == params
        assert "params" not in kwargs

    def test_unsupported_method_raises_value_error(self):
        op = make_operation(method="head")
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            _call_action_operation("https://api.example.com", op, {}, {}, {})

    def test_path_params_are_substituted_in_url(self):
        op = make_operation(method="get", path="/users/{user_id}/posts/{post_id}")
        mock_resp = make_response(json_data={})

        with patch("apps.custom_actions.views.httpx.get") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation(
                server_url="https://api.example.com",
                operation=op,
                params={},
                path_params={"user_id": "42", "post_id": "7"},
                headers={},
            )

        url_called = mock_http.call_args[0][0]
        assert url_called == "https://api.example.com/users/42/posts/7"

    def test_path_params_with_special_chars_are_encoded(self):
        """Path param values containing slashes or traversal sequences are URL-encoded."""
        op = make_operation(method="get", path="/users/{user_id}")
        mock_resp = make_response(json_data={})

        with patch("apps.custom_actions.views.httpx.get") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation(
                server_url="https://api.example.com",
                operation=op,
                params={},
                path_params={"user_id": "../admin"},
                headers={},
            )

        url_called = mock_http.call_args[0][0]
        assert url_called == "https://api.example.com/users/..%2Fadmin"
        assert "../admin" not in url_called

    def test_server_url_trailing_slash_is_stripped(self):
        op = make_operation(method="get", path="/items")
        mock_resp = make_response(json_data={})

        with patch("apps.custom_actions.views.httpx.get") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation("https://api.example.com/", op, {}, {}, {})

        url_called = mock_http.call_args[0][0]
        assert url_called == "https://api.example.com/items"

    def test_auth_headers_are_forwarded(self):
        op = make_operation(method="get")
        mock_resp = make_response(json_data={})
        headers = {"Authorization": "Bearer token123"}

        with patch("apps.custom_actions.views.httpx.get") as mock_http:
            mock_http.return_value = mock_resp
            _call_action_operation("https://api.example.com", op, {}, {}, headers)

        _, kwargs = mock_http.call_args
        assert kwargs["headers"] == headers

    def test_json_response_is_parsed(self):
        op = make_operation(method="get")
        mock_resp = make_response(status_code=200, json_data={"key": "value"})

        with patch("apps.custom_actions.views.httpx.get", return_value=mock_resp):
            result = _call_action_operation("https://api.example.com", op, {}, {}, {})

        assert result == {"status_code": 200, "body": {"key": "value"}, "is_json": True}

    def test_non_json_response_returns_text(self):
        op = make_operation(method="get")
        mock_resp = make_response(status_code=200, text="plain text response")

        with patch("apps.custom_actions.views.httpx.get", return_value=mock_resp):
            result = _call_action_operation("https://api.example.com", op, {}, {}, {})

        assert result == {"status_code": 200, "body": "plain text response", "is_json": False}

    def test_returns_status_code_from_response(self):
        op = make_operation(method="get")
        mock_resp = make_response(status_code=404, json_data={"detail": "not found"})

        with patch("apps.custom_actions.views.httpx.get", return_value=mock_resp):
            result = _call_action_operation("https://api.example.com", op, {}, {}, {})

        assert result["status_code"] == 404


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "auth_provider",
    [
        pytest.param(None, id="no-provider"),
        pytest.param({"type": "bearer", "config": {"token": "secret"}}, id="bearer"),
    ],
)
def test_endpoint_tester_sends_outbound_user_agent(auth_provider, team_with_users, client, httpx_mock, settings):
    settings.OUTBOUND_USER_AGENT = "TestAgent/1.0"
    provider = AuthProviderFactory.create(team=team_with_users, **auth_provider) if auth_provider else None
    action = CustomActionFactory.create(team=team_with_users, auth_provider=provider)
    httpx_mock.add_response(url="https://api.weather.com/weather?location=Cape+Town", json={"ok": True})
    client.force_login(team_with_users.members.first())

    with override_flag(Flags.TESTING_CUSTOM_ACTIONS.slug, active=True):
        response = client.post(
            reverse("custom_actions:test_endpoint", args=[team_with_users.slug, action.pk]),
            data={"operation_id": "weather_get", "params": {"location": "Cape Town"}},
            content_type="application/json",
        )

    assert response.status_code == 200, response.content
    assert httpx_mock.get_request().headers["User-Agent"] == "TestAgent/1.0"
