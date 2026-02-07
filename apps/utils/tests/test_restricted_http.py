from unittest.mock import MagicMock, PropertyMock, patch

import httpx
import pytest

from apps.utils.restricted_http import (
    HttpAuthProviderError,
    HttpConnectionError,
    HttpError,
    HttpInvalidURL,
    HttpRequestLimitExceeded,
    HttpRequestTooLarge,
    HttpResponseTooLarge,
    HttpTimeoutError,
    RestrictedHttpClient,
)


@pytest.fixture()
def client():
    return RestrictedHttpClient()


@pytest.fixture()
def mock_validate_url():
    with patch("apps.utils.restricted_http.validate_user_input_url"):
        yield


# --- Basic Request Tests ---


class TestBasicRequests:
    def test_get_request(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"key": "value"})
        response = client.get("https://api.example.com/data")
        assert response["status_code"] == 200
        assert response["json"] == {"key": "value"}
        assert response["is_success"] is True
        assert response["is_error"] is False

    def test_post_request_with_json(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"created": True}, status_code=201)
        response = client.post("https://api.example.com/data", json={"name": "test"})
        assert response["status_code"] == 201
        assert response["json"] == {"created": True}
        assert response["is_success"] is True

    def test_post_request_with_data(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(text="ok")
        response = client.post("https://api.example.com/data", data={"field": "value"})
        assert response["status_code"] == 200
        assert response["text"] == "ok"

    def test_put_request(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"updated": True})
        response = client.put("https://api.example.com/data/1", json={"name": "updated"})
        assert response["status_code"] == 200

    def test_patch_request(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"patched": True})
        response = client.patch("https://api.example.com/data/1", json={"name": "patched"})
        assert response["status_code"] == 200

    def test_delete_request(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(status_code=204)
        response = client.delete("https://api.example.com/data/1")
        assert response["status_code"] == 204

    def test_response_dict_shape(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"data": 1}, headers={"x-custom": "val"})
        response = client.get("https://api.example.com/data")
        assert set(response.keys()) == {"status_code", "headers", "text", "json", "is_success", "is_error"}
        assert isinstance(response["headers"], dict)
        assert isinstance(response["text"], str)

    def test_non_json_response(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(content=b"plain text", headers={"content-type": "text/plain"})
        response = client.get("https://api.example.com/text")
        assert response["json"] is None
        assert response["text"] == "plain text"

    def test_error_response_not_raised(self, client, mock_validate_url, httpx_mock):
        """Non-2xx responses (except retryable ones) are returned, not raised."""
        httpx_mock.add_response(status_code=404, json={"error": "not found"})
        response = client.get("https://api.example.com/missing")
        assert response["status_code"] == 404
        assert response["is_error"] is True
        assert response["is_success"] is False


# --- URL Validation (SSRF) ---


class TestURLValidation:
    def test_invalid_url_raises(self, client):
        with pytest.raises(HttpInvalidURL):
            client.get("http://127.0.0.1/secret")

    def test_valid_url_passes(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(text="ok")
        response = client.get("https://api.example.com/data")
        assert response["status_code"] == 200

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_url_validation_cached_per_host(self, mock_validate, client, httpx_mock):
        """URL validation (DNS resolution) is only performed once per (hostname, port)."""
        httpx_mock.add_response(text="ok")
        httpx_mock.add_response(text="ok")
        httpx_mock.add_response(text="ok")
        client.get("https://api.example.com/data")
        client.get("https://api.example.com/other")
        client.get("https://api.example.com/third?q=1")
        mock_validate.assert_called_once()

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_url_validation_not_cached_across_hosts(self, mock_validate, client, httpx_mock):
        """Different hostnames each trigger their own validation."""
        httpx_mock.add_response(text="ok")
        httpx_mock.add_response(text="ok")
        client.get("https://api.example.com/data")
        client.get("https://other.example.com/data")
        assert mock_validate.call_count == 2


# --- Blocked Headers ---


class TestBlockedHeaders:
    def test_blocked_headers_stripped(self, client):
        headers = client._prepare_headers(
            {"Host": "evil.com", "Transfer-Encoding": "chunked", "X-Custom": "allowed"}, None
        )
        assert "Host" not in headers
        assert "Transfer-Encoding" not in headers
        assert headers["X-Custom"] == "allowed"

    def test_blocked_headers_case_insensitive(self, client):
        headers = client._prepare_headers({"host": "evil.com", "TRANSFER-ENCODING": "chunked"}, None)
        assert len(headers) == 0


# --- Request Count Limit ---


class TestRequestCountLimit:
    @patch("apps.utils.restricted_http._get_setting")
    def test_request_limit_exceeded(self, mock_setting, client, mock_validate_url, httpx_mock):
        mock_setting.side_effect = lambda name, default: 2 if name == "RESTRICTED_HTTP_MAX_REQUESTS" else default
        httpx_mock.add_response(text="ok")
        httpx_mock.add_response(text="ok")
        client.get("https://api.example.com/1")
        client.get("https://api.example.com/2")
        with pytest.raises(HttpRequestLimitExceeded, match="Request limit of 2 exceeded"):
            client.get("https://api.example.com/3")

    def test_request_count_increments(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(text="ok")
        httpx_mock.add_response(text="ok")
        assert client._request_count == 0
        client.get("https://api.example.com/1")
        assert client._request_count == 1
        client.get("https://api.example.com/2")
        assert client._request_count == 2


# --- Response Size Limit ---


class TestResponseSizeLimit:
    @patch("apps.utils.restricted_http._get_setting")
    def test_response_too_large(self, mock_setting, client, mock_validate_url, httpx_mock):
        mock_setting.side_effect = lambda name, default: 10 if name == "RESTRICTED_HTTP_MAX_RESPONSE_BYTES" else default
        httpx_mock.add_response(content=b"x" * 100)
        with pytest.raises(HttpResponseTooLarge, match="exceeds 10 bytes"):
            client.get("https://api.example.com/large")


# --- Request Body Size Limit ---


class TestRequestBodySizeLimit:
    @patch("apps.utils.restricted_http._get_setting")
    def test_json_body_too_large(self, mock_setting, client, mock_validate_url):
        mock_setting.side_effect = lambda name, default: 10 if name == "RESTRICTED_HTTP_MAX_REQUEST_BYTES" else default
        with pytest.raises(HttpRequestTooLarge, match="exceeds 10 bytes"):
            client.post("https://api.example.com/data", json={"key": "a" * 100})

    @patch("apps.utils.restricted_http._get_setting")
    def test_data_body_too_large(self, mock_setting, client, mock_validate_url):
        mock_setting.side_effect = lambda name, default: 10 if name == "RESTRICTED_HTTP_MAX_REQUEST_BYTES" else default
        with pytest.raises(HttpRequestTooLarge, match="exceeds 10 bytes"):
            client.post("https://api.example.com/data", data=b"x" * 100)


# --- Timeout Clamping ---


class TestTimeoutClamping:
    def test_timeout_clamped_to_min(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(text="ok")
        client.get("https://api.example.com/data", timeout=0.1)

    def test_timeout_clamped_to_max(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(text="ok")
        client.get("https://api.example.com/data", timeout=999)


# --- Mutual Exclusivity ---


class TestMutualExclusivity:
    def test_json_and_data_exclusive(self, client, mock_validate_url):
        with pytest.raises(ValueError, match="mutually exclusive"):
            client.post("https://api.example.com/data", json={"a": 1}, data="b")

    def test_json_and_files_exclusive(self, client, mock_validate_url):
        with pytest.raises(ValueError, match="mutually exclusive"):
            client.post("https://api.example.com/data", json={"a": 1}, files={"file": b"data"})


# --- Redirect Policy ---


class TestRedirectPolicy:
    def test_redirects_not_followed(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(status_code=301, headers={"Location": "https://other.com"})
        response = client.get("https://api.example.com/redirect")
        assert response["status_code"] == 301


# --- Retry Behavior ---


class TestRetryBehavior:
    def test_retries_on_429(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(status_code=429, headers={"Retry-After": "0"})
        httpx_mock.add_response(status_code=429, headers={"Retry-After": "0"})
        httpx_mock.add_response(json={"ok": True})
        response = client.get("https://api.example.com/data")
        assert response["status_code"] == 200
        assert len(httpx_mock.get_requests()) == 3

    def test_retries_on_503(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(status_code=503)
        httpx_mock.add_response(text="ok")
        response = client.get("https://api.example.com/data")
        assert response["status_code"] == 200
        assert len(httpx_mock.get_requests()) == 2

    def test_retries_exhausted_returns_last_response(self, client, mock_validate_url, httpx_mock):
        """When all retries are exhausted on a retryable status, return the response."""
        for _ in range(3):
            httpx_mock.add_response(status_code=429, json={"error": "rate limited"})
        response = client.get("https://api.example.com/data")
        assert response["status_code"] == 429
        assert response["json"] == {"error": "rate limited"}
        assert client._request_count == 3  # 1 initial + 2 retries

    def test_retries_count_toward_limit(self, client, mock_validate_url, httpx_mock):
        """Each retry counts as a request toward the limit."""
        # First call: 3 attempts (all 429, retries exhausted). Second call: 2 attempts then limit hit.
        for _ in range(5):
            httpx_mock.add_response(status_code=429)
        with patch(
            "apps.utils.restricted_http._get_setting",
            side_effect=lambda name, default: 5 if name == "RESTRICTED_HTTP_MAX_REQUESTS" else default,
        ):
            client.get("https://api.example.com/1")  # uses 3 (retries exhausted)
            assert client._request_count == 3
            # Second call: gets 2 more attempts (4, 5) then hits limit on 3rd retry attempt
            with pytest.raises(HttpRequestLimitExceeded):
                client.get("https://api.example.com/2")

    def test_connection_error_retried_then_raised(self, client, mock_validate_url, httpx_mock):
        for _ in range(3):
            httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        with pytest.raises(HttpConnectionError, match="Connection error"):
            client.get("https://api.example.com/data")

    def test_timeout_retried_then_raised(self, client, mock_validate_url, httpx_mock):
        for _ in range(3):
            httpx_mock.add_exception(httpx.ConnectTimeout("timed out"))
        with pytest.raises(HttpTimeoutError, match="timed out"):
            client.get("https://api.example.com/data")


# --- Auth Provider Integration ---


@pytest.mark.django_db()
class TestAuthProviderIntegration:
    def test_auth_not_available_without_team(self, mock_validate_url):
        client = RestrictedHttpClient(team=None)
        with pytest.raises(HttpAuthProviderError, match="not available"):
            client.get("https://api.example.com/data", auth="My Provider")

    def test_auth_provider_not_found(self, mock_validate_url):
        from apps.utils.factories.team import TeamFactory

        team = TeamFactory()
        client = RestrictedHttpClient(team=team)
        with pytest.raises(HttpAuthProviderError, match="not found"):
            client.get("https://api.example.com/data", auth="Nonexistent Provider")

    def test_auth_provider_found_and_headers_injected(self):
        from apps.utils.factories.service_provider_factories import AuthProviderFactory

        provider = AuthProviderFactory(
            name="My API Key",
            type="api_key",
            config={"key": "X-Api-Key", "value": "secret123"},
        )
        client = RestrictedHttpClient(team=provider.team)

        headers = client._resolve_auth_headers("My API Key")
        assert headers.get("X-Api-Key") or headers.get("x-api-key")

    def test_auth_provider_cached(self):
        from apps.utils.factories.service_provider_factories import AuthProviderFactory

        provider = AuthProviderFactory(
            name="Cached Provider",
            type="bearer",
            config={"token": "bearer-token"},
        )
        client = RestrictedHttpClient(team=provider.team)

        headers1 = client._resolve_auth_headers("Cached Provider")
        headers2 = client._resolve_auth_headers("Cached Provider")
        assert headers1 == headers2
        assert "Cached Provider" in client._auth_cache

    def test_auth_headers_take_precedence(self):
        from apps.utils.factories.service_provider_factories import AuthProviderFactory

        provider = AuthProviderFactory(
            name="Bearer Auth",
            type="bearer",
            config={"token": "real-token"},
        )
        client = RestrictedHttpClient(team=provider.team)

        # User tries to set Authorization, but auth provider overrides it
        headers = client._prepare_headers({"Authorization": "fake"}, "Bearer Auth")
        # The user's "Authorization" key should be dropped in favor of the auth provider's
        auth_values = [v for k, v in headers.items() if k.lower() == "authorization"]
        assert len(auth_values) == 1
        assert "Bearer real-token" in auth_values[0]

    def test_no_auth_sends_no_auth_headers(self):
        client = RestrictedHttpClient()
        headers = client._prepare_headers({"X-Custom": "value"}, None)
        assert "Authorization" not in headers
        assert headers == {"X-Custom": "value"}

    def test_cross_team_lookup_blocked(self):
        from apps.utils.factories.service_provider_factories import AuthProviderFactory
        from apps.utils.factories.team import TeamFactory

        AuthProviderFactory(name="Team A Provider")
        other_team = TeamFactory()

        client = RestrictedHttpClient(team=other_team)
        with pytest.raises(HttpAuthProviderError, match="not found"):
            client._resolve_auth_headers("Team A Provider")


# --- File Upload ---


class TestFileUpload:
    def test_bytes_file_upload(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"uploaded": True})
        response = client.post(
            "https://api.example.com/upload",
            files={"file": b"raw bytes content"},
        )
        assert response["status_code"] == 200

    def test_tuple_file_upload(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"uploaded": True})
        response = client.post(
            "https://api.example.com/upload",
            files={"file": ("report.pdf", b"pdf content", "application/pdf")},
        )
        assert response["status_code"] == 200

    def test_multiple_files_as_list(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"uploaded": True})
        response = client.post(
            "https://api.example.com/upload",
            files=[("files", b"file1"), ("files", b"file2")],
        )
        assert response["status_code"] == 200

    def test_mixed_files_and_data(self, client, mock_validate_url, httpx_mock):
        httpx_mock.add_response(json={"uploaded": True})
        response = client.post(
            "https://api.example.com/upload",
            files={"file": b"content"},
            data={"description": "test file"},
        )
        assert response["status_code"] == 200

    @patch("apps.utils.restricted_http._get_setting")
    def test_file_size_limit(self, mock_setting, client, mock_validate_url):
        mock_setting.side_effect = (
            lambda name, default: 10 if name == "RESTRICTED_HTTP_MAX_FILE_UPLOAD_BYTES" else default
        )
        with pytest.raises(HttpRequestTooLarge, match="File upload exceeds"):
            client.post(
                "https://api.example.com/upload",
                files={"file": b"x" * 100},
            )

    def test_unsupported_file_type(self, client, mock_validate_url):
        with pytest.raises(ValueError, match="Unsupported file value type"):
            client.post(
                "https://api.example.com/upload",
                files={"file": 12345},
            )

    def test_attachment_resolved(self, client):
        """Test that Attachment objects are resolved correctly."""
        from io import BytesIO

        from apps.channels.datamodels import Attachment

        mock_file = MagicMock()
        mock_file.file.open.return_value = BytesIO(b"file content")

        attachment = Attachment(
            file_id=1,
            type="ocs_attachments",
            name="report.pdf",
            size=12,
            content_type="application/pdf",
            download_link="https://example.com/file",
        )

        with patch.object(type(attachment), "_file", new_callable=PropertyMock, return_value=mock_file):
            entry, size, handles = client._resolve_file_entry("file", attachment)
            assert entry[0] == "report.pdf"
            assert entry[2] == "application/pdf"
            assert size == 12
            assert len(handles) == 1

    def test_attachment_missing_file(self, client):
        from apps.channels.datamodels import Attachment

        attachment = Attachment(
            file_id=999,
            type="ocs_attachments",
            name="missing.pdf",
            size=0,
            content_type="application/pdf",
            download_link="https://example.com/file",
        )

        with patch.object(type(attachment), "_file", new_callable=PropertyMock, return_value=None):
            with pytest.raises(HttpError, match="not found"):
                client._resolve_file_entry("file", attachment)


# --- Error Hierarchy ---


class TestErrorHierarchy:
    def test_all_errors_inherit_from_http_error(self):
        assert issubclass(HttpRequestLimitExceeded, HttpError)
        assert issubclass(HttpRequestTooLarge, HttpError)
        assert issubclass(HttpResponseTooLarge, HttpError)
        assert issubclass(HttpConnectionError, HttpError)
        assert issubclass(HttpTimeoutError, HttpError)
        assert issubclass(HttpInvalidURL, HttpError)
        assert issubclass(HttpAuthProviderError, HttpError)
