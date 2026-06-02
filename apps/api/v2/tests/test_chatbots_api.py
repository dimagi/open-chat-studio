import pytest
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    exp = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    LlmProviderFactory.create(team=exp.team)
    return exp


def test_v2_chatbot_reverse_is_versioned():
    """The v2 chatbots route lives under its own namespace and keeps the /api/v2/ prefix."""
    assert reverse("api:v2:chatbot-list") == "/api/v2/chatbots/"


def test_v2_chatbot_detail_resolves():
    match = resolve("/api/v2/chatbots/123e4567-e89b-12d3-a456-426614174000/")
    assert match.url_name == "chatbot-detail"


@pytest.mark.django_db()
def test_v2_experiments_still_404(experiment):
    """v2 renamed the surface to /chatbots/; the old /experiments/ name must not exist in v2."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    assert client.get("/api/v2/experiments/").status_code == 404


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_chatbots(auth_method, experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)
    response = client.get(reverse("api:v2:chatbot-list"))
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == str(experiment.public_id)
    assert results[0]["name"] == experiment.name


@pytest.mark.django_db()
def test_retrieve_chatbot_by_public_id(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 200
    assert response.json()["id"] == str(experiment.public_id)


@pytest.mark.django_db()
def test_list_chatbots_only_working_versions(experiment):
    """Versions (non-working) are not listed at the top level."""
    user = experiment.team.members.first()
    experiment.create_new_version()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:v2:chatbot-list"))
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == str(experiment.public_id)


@pytest.mark.django_db()
def test_chatbot_other_team_404(experiment):
    other_team = TeamWithUsersFactory.create()
    other_user = other_team.members.first()
    client = ApiTestClient(other_user, other_team)
    response = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_chatbot_anonymous_401():
    response = APIClient().get("/api/v2/chatbots/")
    assert response.status_code == 401
