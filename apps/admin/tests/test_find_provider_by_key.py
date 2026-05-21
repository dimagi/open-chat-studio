import pytest
from django.urls import reverse

from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def staff_client(client):
    user = UserFactory.create(is_staff=True)
    client.force_login(user)
    return client


@pytest.fixture()
def anon_client(client):
    user = UserFactory.create()
    client.force_login(user)
    return client


@pytest.mark.django_db()
def test_requires_staff(anon_client):
    response = anon_client.get(reverse("ocs_admin:find_provider_by_key"))
    assert response.status_code == 302
    assert response.url.startswith("/404")


@pytest.mark.django_db()
def test_get_renders_form(staff_client):
    response = staff_client.get(reverse("ocs_admin:find_provider_by_key"))
    assert response.status_code == 200
    assert b"Find provider by API key" in response.content


@pytest.mark.django_db()
def test_search_returns_matches(staff_client):
    team = TeamFactory.create()
    provider = LlmProviderFactory(
        team=team,
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "sk-ant-secret-AbCdEf"},
    )
    response = staff_client.post(
        reverse("ocs_admin:find_provider_by_key"),
        data={"provider_type": "llm", "key": "AbCdEf", "match": "suffix"},
    )
    assert response.status_code == 200
    results = response.context["results"]
    assert any(entry["provider"].pk == provider.pk for entry in results)


@pytest.mark.django_db()
def test_search_no_matches(staff_client):
    LlmProviderFactory(
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "sk-ant-real-key"},
    )
    response = staff_client.post(
        reverse("ocs_admin:find_provider_by_key"),
        data={"provider_type": "llm", "key": "nope", "match": "exact"},
    )
    assert response.status_code == 200
    assert response.context["results"] == []
    assert b"No providers matched" in response.content


@pytest.mark.django_db()
def test_invalid_form_does_not_search(staff_client):
    response = staff_client.post(
        reverse("ocs_admin:find_provider_by_key"),
        data={"provider_type": "llm", "key": "", "match": "exact"},
    )
    assert response.status_code == 200
    assert response.context["results"] is None
