import httpx
import openai
import pytest

from apps.service_providers.exceptions import provider_error_message


class _FakeProviderError(Exception):
    def __init__(self, message, body):
        super().__init__(message)
        self.body = body


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(
            _FakeProviderError(
                "Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-abc'}}",
                {"message": "Incorrect API key provided: sk-abc", "code": "invalid_api_key"},
            ),
            "Incorrect API key provided: sk-abc",
            id="mapping-body-with-message",
        ),
        pytest.param(
            _FakeProviderError("Error code: 500", {"code": "server_error"}),
            "Error code: 500",
            id="mapping-body-without-message",
        ),
        pytest.param(
            _FakeProviderError("Error code: 502", "<html>Bad Gateway</html>"),
            "Error code: 502",
            id="string-body",
        ),
        pytest.param(
            _FakeProviderError("Connection error.", None),
            "Connection error.",
            id="none-body",
        ),
        pytest.param(
            ValueError("something local went wrong"),
            "something local went wrong",
            id="no-body-attribute",
        ),
    ],
)
def test_provider_error_message(exc, expected):
    assert provider_error_message(exc) == expected


def test_provider_error_message_ignores_a_non_string_message():
    """A message that is not a string would reach format_failure_reason and be rendered
    by str() anyway, so the exception's own text is the more useful of the two."""
    exc = _FakeProviderError("Error code: 400", {"message": {"nested": "object"}})

    assert provider_error_message(exc) == "Error code: 400"


def test_provider_error_message_reads_a_real_openai_exception():
    """Pins the shape the helper depends on: the OpenAI SDK unwraps the response payload and
    hands the inner error object to the exception, so the message sits at `body["message"]`
    rather than `body["error"]["message"]`."""
    response = httpx.Response(
        401,
        request=httpx.Request("GET", "https://api.openai.com/v1/files/f"),
        json={"error": {"message": "Incorrect API key provided: sk-abc"}},
    )
    exc = openai.AuthenticationError("Error code: 401", response=response, body=response.json()["error"])

    assert provider_error_message(exc) == "Incorrect API key provided: sk-abc"
