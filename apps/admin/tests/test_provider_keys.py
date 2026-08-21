import json

import pytest
from django.urls import reverse

from apps.admin.provider_keys import mask_secret
from apps.service_providers.models import LlmProviderTypes
from apps.users.models import CustomUser
from apps.utils.factories.service_provider_factories import LlmProviderFactory, TraceProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

OPENAI_KEY = "sk-abcdefghijklJrYA"
ANTHROPIC_KEY = "sk-ant-api03-cLVxxxxxxxxxxlAAA"


@pytest.mark.parametrize(
    ("provider_type", "secret", "expected"),
    [
        pytest.param("openai", OPENAI_KEY, "sk-...JrYA", id="openai"),
        pytest.param("azure", OPENAI_KEY, "sk-...JrYA", id="azure"),
        pytest.param("anthropic", ANTHROPIC_KEY, "sk-ant-api03-cLV...lAAA", id="anthropic"),
        pytest.param("deepseek", "sk-somethingXYZW", "...XYZW", id="generic-fallback"),
        pytest.param("openai", "", "", id="empty"),
        pytest.param("openai", "abc", "...abc", id="too-short"),
        pytest.param("google_vertex_ai", {"type": "service_account"}, "", id="non-string-dict"),
        pytest.param("openai", None, "", id="none"),
    ],
)
def test_mask_secret(provider_type, secret, expected):
    assert mask_secret(secret, provider_type) == expected


@pytest.mark.django_db()
def test_non_superuser_blocked(client):
    client.force_login(CustomUser.objects.create(username="staff@acme.com", is_staff=True))
    response = client.get(reverse("ocs_admin:provider_keys_api"))
    assert response.status_code == 302  # user_passes_test redirects to login_url


@pytest.mark.django_db()
def test_masks_keys_and_never_leaks_secret(superuser_client):
    LlmProviderFactory(
        type=str(LlmProviderTypes.openai),
        config={"openai_api_key": OPENAI_KEY, "openai_organization": "org-x"},
    )
    LlmProviderFactory(type=str(LlmProviderTypes.anthropic), config={"anthropic_api_key": ANTHROPIC_KEY})

    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    assert response.status_code == 200
    providers = response.json()["providers"]
    by_type = {p["provider_type"]: p for p in providers}
    assert by_type["openai"]["masked_key"] == "sk-...JrYA"
    assert by_type["openai"]["organization"] == "org-x"
    assert by_type["anthropic"]["masked_key"] == "sk-ant-api03-cLV...lAAA"
    # Every record carries the team so the report can attribute cost, plus the
    # team slug + metadata so a zero-usage team is still labelled in the report.
    assert all(p["team_id"] and p["team_name"] and p["team_slug"] for p in providers)
    assert all("metadata" in p for p in providers)
    assert "metadata_fields" in response.json()

    body = response.content.decode()
    assert OPENAI_KEY not in body
    assert ANTHROPIC_KEY not in body


@pytest.mark.django_db()
def test_exposes_team_slug_and_filtered_metadata(superuser_client, settings):
    # A team can own a key but have no usage in a reporting window, so the key
    # registry must carry its label metadata (only the configured fields).
    settings.TEAM_METADATA_FIELDS = [
        {"key": "team_owner", "label": "Team Owner"},
        {"key": "region", "label": "Region"},
    ]
    team = TeamFactory(name="Alpha", metadata={"team_owner": "Jia", "internal_only": "hidden"})
    LlmProviderFactory(team=team, type=str(LlmProviderTypes.openai), config={"openai_api_key": OPENAI_KEY})

    payload = superuser_client.get(reverse("ocs_admin:provider_keys_api")).json()

    assert payload["metadata_fields"] == [
        {"key": "team_owner", "label": "Team Owner", "type": "text"},
        {"key": "region", "label": "Region", "type": "text"},
    ]
    record = payload["providers"][0]
    assert record["team_slug"] == team.slug
    # Only configured fields are exposed; unconfigured keys stay hidden, missing ones blank.
    assert record["metadata"] == {"team_owner": "Jia", "region": ""}


@pytest.mark.django_db()
def test_exposes_team_creator_for_llm_and_trace_providers(superuser_client):
    creator = UserFactory(username="creator", email="creator@example.com")
    team = TeamFactory(name="Alpha", created_by=creator)
    LlmProviderFactory(team=team, type=str(LlmProviderTypes.openai), config={"openai_api_key": OPENAI_KEY})
    TraceProviderFactory(team=team)

    payload = superuser_client.get(reverse("ocs_admin:provider_keys_api")).json()

    expected = {"id": creator.id, "username": "creator", "email": "creator@example.com"}
    assert payload["providers"][0]["created_by"] == expected
    assert payload["trace_providers"][0]["created_by"] == expected


@pytest.mark.django_db()
def test_vertex_dict_credentials_do_not_crash(superuser_client):
    # Vertex stores credentials as a JSON dict, not a string key; the endpoint
    # must not choke on it (masks to an empty fingerprint).
    LlmProviderFactory(
        type=str(LlmProviderTypes.google_vertex_ai),
        config={"credentials_json": {"type": "service_account", "private_key_id": "abc"}},
    )
    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    assert response.status_code == 200
    vertex = {p["provider_type"]: p for p in response.json()["providers"]}["google_vertex_ai"]
    assert vertex["masked_key"] == ""


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("credentials", "expected"),
    [
        pytest.param({"type": "service_account", "project_id": "ocs-vertex"}, "ocs-vertex", id="dict"),
        pytest.param('{"project_id": "ocs-vertex"}', "ocs-vertex", id="json-string"),
        pytest.param({"type": "service_account"}, None, id="no-project-id"),
        pytest.param("not json", None, id="unparseable"),
        pytest.param(None, None, id="missing"),
    ],
)
def test_vertex_exposes_gcp_project(superuser_client, credentials, expected):
    """Vertex has no key fingerprint to join on, so the GCP project is the join key
    against Cloud Billing. It is an identifier, not a credential."""
    LlmProviderFactory(
        type=str(LlmProviderTypes.google_vertex_ai),
        config={"credentials_json": credentials},
    )
    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    vertex = {p["provider_type"]: p for p in response.json()["providers"]}["google_vertex_ai"]
    assert vertex["cloud_project"] == expected


@pytest.mark.django_db()
def test_non_vertex_provider_never_reports_a_cloud_project(superuser_client):
    """`config` is a free-form JSON blob, so any provider could carry a
    `credentials_json`. Only Vertex bills by project, and handing a spend report a
    project id for anything else would attribute that cost to the wrong team."""
    LlmProviderFactory(
        type=str(LlmProviderTypes.openai),
        config={"openai_api_key": OPENAI_KEY, "credentials_json": {"project_id": "not-vertex"}},
    )
    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    openai = {p["provider_type"]: p for p in response.json()["providers"]}["openai"]
    assert openai["cloud_project"] is None


@pytest.mark.django_db()
def test_lists_trace_providers_with_project_mapping(superuser_client):
    team = TeamFactory(name="Alpha")
    TraceProviderFactory(
        team=team,
        name="Langfuse",
        config={"public_key": "pk-lf-1", "secret_key": "sk-lf-1", "host": "https://cloud.langfuse.com"},
        metadata={
            "project_id": "proj-123",
            "project_name": "alpha-bot",
            "organization_id": "org-dimagi",
            "organization_name": "Dimagi",
        },
    )

    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["trace_providers"]) == 1
    record = payload["trace_providers"][0]
    assert record["team_name"] == "Alpha"
    assert record["project_id"] == "proj-123"
    assert record["organization_id"] == "org-dimagi"
    assert record["host"] == "https://cloud.langfuse.com"
    # The key pair is the whole reason config is encrypted; it must not ride along.
    assert "secret_key" not in json.dumps(record)
    assert "pk-lf-1" not in json.dumps(record)


@pytest.mark.django_db()
def test_trace_provider_without_metadata_reports_blank_project(superuser_client):
    """Metadata is best-effort at save time (a Langfuse outage leaves it empty), so the
    record still appears — with no project to join on — rather than vanishing."""
    TraceProviderFactory(team=TeamFactory(name="Alpha"), metadata={})

    response = superuser_client.get(reverse("ocs_admin:provider_keys_api"))

    record = response.json()["trace_providers"][0]
    assert record["project_id"] == ""
    assert record["organization_id"] == ""
