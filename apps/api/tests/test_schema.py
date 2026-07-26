import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.api.schema import _swap_host


def test_schema_filters():
    c = Client()
    response = c.get("/api/schema/")
    response_yaml = response.content.decode("utf-8")
    assert "/cms/" not in response_yaml


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            'http://api.example.org/accounts/?cursor=cD00ODY%3D"',
            "https://ocs.example/api/?cursor=cD00ODY%3D",
            id="pagination-cursor-drops-accounts-path-and-stray-quote",
        ),
        pytest.param(
            "https://example.com/api/v2/chatbots/123/",
            "https://ocs.example/api/v2/chatbots/123/",
            id="url-field-host-swapped-path-kept",
        ),
        pytest.param("http://example.com", "https://ocs.example", id="bare-placeholder-host"),
        pytest.param("test@example.com", "test@example.com", id="email-left-untouched"),
        pytest.param("https://docs.openchatstudio.com/api/", "https://docs.openchatstudio.com/api/", id="other-host"),
    ],
)
def test_swap_host(value, expected):
    assert _swap_host(value, "https://ocs.example") == expected


def test_served_schema_uses_request_host():
    """The live-served schema points example URLs at the requesting deployment's host, not the
    ``example.com``/``example.org`` placeholders baked into the committed schema files."""
    response = Client().get("/api/v2/schema/", HTTP_HOST="chatbots.example.test")
    schema = yaml.safe_load(response.content)
    chatbot = schema["components"]["schemas"]["Chatbot"]["properties"]["url"]
    assert chatbot["example"] == "http://chatbots.example.test/api/v2/chatbots/123e4567-e89b-12d3-a456-426614174000/"

    pagination = schema["components"]["schemas"]["PaginatedChatbotList"]["properties"]["next"]
    assert pagination["example"] == "http://chatbots.example.test/api/?cursor=cD00ODY%3D"

    body = response.content.decode()
    assert "api.example.org" not in body
    assert "example.com" not in body


@pytest.mark.parametrize(
    "version",
    [
        pytest.param("v1", id="v1"),
        pytest.param("v2", id="v2"),
        pytest.param("export", id="export"),
    ],
)
def test_schema_is_up_to_date_and_valid(pytestconfig, tmp_path, version):
    """If this test fails run `inv schema` to update the schema."""
    path = tmp_path / f"{version}.yml"
    call_command("spectacular", api_version=version, validate=True, file=str(path))
    with open(path) as f:
        new_schema = yaml.safe_load(f)

    with open(f"{pytestconfig.rootdir}/api-schemas/{version}.yml") as f:
        old_schema = yaml.safe_load(f)

    assert old_schema == new_schema
