import httpx
import pytest
from django.contrib.sites.models import Site

from apps.service_providers.auth_service import (
    ApiKeyAuthService,
    AuthService,
    BasicAuthService,
    BearerTokenAuthService,
    CommCareAuthService,
)
from apps.service_providers.auth_service.main import outbound_user_agent
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


USER_AGENT = "TestAgent/1.0"

AUTH_SERVICES = [
    pytest.param(AuthService(), id="no-provider"),
    pytest.param(BasicAuthService(username="user", password="secret"), id="basic"),
    pytest.param(ApiKeyAuthService(key="X-Api-Key", value="secret"), id="api-key"),
    pytest.param(BearerTokenAuthService(token="secret"), id="bearer"),
    pytest.param(CommCareAuthService(username="user", api_key="secret"), id="commcare"),
]


@pytest.fixture()
def site_domain(settings):
    settings.OUTBOUND_USER_AGENT = ""
    Site.objects.update_or_create(pk=settings.SITE_ID, defaults={"domain": "chat.example.org", "name": "OCS"})
    Site.objects.clear_cache()
    yield
    Site.objects.clear_cache()


@pytest.mark.django_db()
@pytest.mark.usefixtures("site_domain")
def test_outbound_user_agent_names_the_project_and_site():
    assert outbound_user_agent() == "OpenChatStudio (+http://chat.example.org)"


def test_outbound_user_agent_setting_replaces_the_default(settings):
    settings.OUTBOUND_USER_AGENT = "SelfHosted/2.0"
    assert outbound_user_agent() == "SelfHosted/2.0"


@pytest.mark.parametrize("auth_service", AUTH_SERVICES)
def test_http_client_sends_outbound_user_agent(auth_service, settings, httpx_mock):
    settings.OUTBOUND_USER_AGENT = USER_AGENT
    httpx_mock.add_response(url="https://example.com/")
    with auth_service.get_http_client() as client:
        client.get("https://example.com/")

    assert httpx_mock.get_request().headers["User-Agent"] == USER_AGENT


@pytest.mark.parametrize("auth_service", AUTH_SERVICES)
def test_auth_headers_carry_outbound_user_agent(auth_service, settings, httpx_mock):
    settings.OUTBOUND_USER_AGENT = USER_AGENT
    httpx_mock.add_response(url="https://example.com/")
    httpx.get("https://example.com/", headers=auth_service.get_auth_headers())

    assert httpx_mock.get_request().headers["User-Agent"] == USER_AGENT
