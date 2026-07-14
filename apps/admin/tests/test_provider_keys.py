import pytest
from django.urls import reverse

from apps.admin.provider_keys import mask_secret
from apps.service_providers.models import LlmProviderTypes
from apps.users.models import CustomUser
from apps.utils.factories.service_provider_factories import LlmProviderFactory

OPENAI_KEY = "sk-abcdefghijklJrYA"
ANTHROPIC_KEY = "sk-ant-api03-cLVxxxxxxxxxxlAAA"


@pytest.fixture()
def superuser_client(client):
    user = CustomUser.objects.create(username="admin@acme.com", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


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
    # Every record carries the team so the report can attribute cost.
    assert all(p["team_id"] and p["team_name"] for p in providers)

    body = response.content.decode()
    assert OPENAI_KEY not in body
    assert ANTHROPIC_KEY not in body


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
