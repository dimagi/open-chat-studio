import os

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.api.schema import _swap_host
from apps.api.v2.pipeline_edit.serializers import MAX_WIRES_PER_CALL

VERSIONS = [pytest.param("v1", id="v1"), pytest.param("v2", id="v2"), pytest.param("export", id="export")]


@pytest.mark.django_db()
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


@pytest.mark.django_db()
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


def _drop_oidc_scopes(schema):
    """Remove the OIDC-only scopes from ``schema``'s security schemes, in place.

    The committed schemas are generated with ``OIDC_RSA_PRIVATE_KEY`` unset -- that is what
    ``.github/workflows/update-generated-files.yml`` and ``inv schema`` both run with. A developer
    who sets a key of their own makes ``config.settings`` add ``openid``/``profile`` to
    ``OAUTH2_PROVIDER["SCOPES"]`` and so to every OAuth2 flow in the generated schema. Dropping just
    those scopes keeps the comparison sensitive to real schema drift instead of to local env.

    Only the freshly-generated schema is normalized, never the committed one: a committed schema
    that *contains* these scopes is itself the bug, and must keep failing.
    """
    for scheme in schema.get("components", {}).get("securitySchemes", {}).values():
        for flow in (scheme.get("flows") or {}).values():
            scopes = flow.get("scopes")
            if not scopes:
                continue
            for scope in settings.OIDC_ONLY_SCOPES:
                scopes.pop(scope, None)
    return schema


@pytest.mark.parametrize("version", VERSIONS)
def test_schema_is_up_to_date_and_valid(pytestconfig, tmp_path, version):
    """If this test fails run `inv schema` to update the schema."""
    path = tmp_path / f"{version}.yml"
    call_command("spectacular", api_version=version, validate=True, file=str(path))
    with open(path) as f:
        new_schema = yaml.safe_load(f)

    with open(f"{pytestconfig.rootdir}/api-schemas/{version}.yml") as f:
        old_schema = yaml.safe_load(f)

    if settings.OIDC_RSA_PRIVATE_KEY:
        _drop_oidc_scopes(new_schema)

    assert old_schema == new_schema


def test_the_wire_body_publishes_the_limit_it_enforces(pytestconfig):
    """The endpoint refuses a body naming more than `MAX_WIRES_PER_CALL` wires, so the schema has to
    say so: the consumer this API is built for reads the schema rather than the prose.

    Asserted against the committed file, and against the constant rather than a literal, so raising
    the limit without regenerating fails here naming the limit -- where
    `test_schema_is_up_to_date_and_valid` would only report that the file drifted.
    """
    with open(f"{pytestconfig.rootdir}/api-schemas/v2.yml") as f:
        schema = yaml.safe_load(f)

    wires = schema["components"]["schemas"]["WireBody"]["properties"]["wires"]

    assert (wires["minItems"], wires["maxItems"]) == (1, MAX_WIRES_PER_CALL)


def test_drop_oidc_scopes_only_removes_oidc_scopes():
    """Only the OIDC-only scopes are stripped -- everything else must survive, so that genuine
    schema drift still fails ``test_schema_is_up_to_date_and_valid``."""
    schema = {
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "flows": {
                        "authorizationCode": {
                            "scopes": {
                                "chatbots:read": "List and Retrieve Chatbot Data",
                                "openid": "OpenID Connect scope",
                                "profile": "User Profile",
                            }
                        }
                    }
                },
                "chatOAuth2": {"flows": {"clientCredentials": {"scopes": {"chat:start": "Start a chat session"}}}},
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-api-key"},
            }
        }
    }

    _drop_oidc_scopes(schema)

    schemes = schema["components"]["securitySchemes"]
    assert schemes["OAuth2"]["flows"]["authorizationCode"]["scopes"] == {
        "chatbots:read": "List and Retrieve Chatbot Data"
    }
    assert schemes["chatOAuth2"]["flows"]["clientCredentials"]["scopes"] == {"chat:start": "Start a chat session"}
    assert schemes["apiKeyAuth"] == {"type": "apiKey", "in": "header", "name": "X-api-key"}


@pytest.mark.parametrize("version", VERSIONS)
def test_schema_generates_without_warnings(version):
    """Warnings mean a view or serializer could not be resolved. See `@extend_schema`.

    The emitted warnings are printed to stderr, so pytest shows them on failure.
    """
    call_command("spectacular", api_version=version, fail_on_warn=True, file=os.devnull)
