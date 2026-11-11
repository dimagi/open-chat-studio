import pytest

from apps.service_providers.forms import ApiKeyAuthConfigForm
from apps.service_providers.models import AuthProviderType


def test_commcare_auth_provider(team_with_users):
    _test_provider(
        team_with_users,
        AuthProviderType.commcare,
        data={
            "username": "demo",
            "api_key": "123123",
        },
    )


def _test_provider(team, provider_type: AuthProviderType, data):
    form = provider_type.form_cls(team, data=data)
    assert form.is_valid()
    assert form.cleaned_data == data


@pytest.mark.parametrize(
    "header_name",
    [
        pytest.param("X-Api-Key", id="dashed"),
        pytest.param("Authorization", id="standard-header"),
        pytest.param("x_api.key~1", id="token-punctuation"),
    ],
)
def test_api_key_header_name_is_sent_on_the_request(header_name, httpx_mock):
    form = ApiKeyAuthConfigForm(None, data={"key": header_name, "value": "secret"})
    assert form.is_valid(), form.errors

    httpx_mock.add_response(url="https://example.com/")
    auth_service = AuthProviderType.api_key.get_auth_service(form.cleaned_data)
    with auth_service.get_http_client() as client:
        client.get("https://example.com/")

    assert httpx_mock.get_request().headers[header_name] == "secret"


@pytest.mark.parametrize(
    "header_name",
    [
        pytest.param("X Api Key", id="space"),
        pytest.param("X-Api-Key:", id="colon"),
        pytest.param("X-Api-Key\r\nX-Injected", id="line-break"),
        pytest.param("X-(Key)", id="separator"),
        pytest.param("X-Clé", id="non-ascii"),
        pytest.param("Host", id="host"),
        pytest.param("TRANSFER-ENCODING", id="transfer-encoding"),
    ],
)
def test_api_key_header_name_is_rejected(header_name):
    form = ApiKeyAuthConfigForm(None, data={"key": header_name, "value": "secret"})
    assert not form.is_valid()
    assert list(form.errors) == ["key"]
