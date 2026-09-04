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
