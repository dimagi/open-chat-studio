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


def test_v2_chatbot_detail_resolves():
    match = resolve("/api/v2/chatbots/123e4567-e89b-12d3-a456-426614174000/")
    assert match.url_name == "chatbot-detail"


@pytest.mark.django_db()
def test_v2_experiments_still_404(experiment):
    """v2 uses /chatbots/; the old /experiments/ path must not exist under v2."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    assert client.get("/api/v2/experiments/").status_code == 404


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_retrieve_chatbot_by_public_id(auth_method, experiment):
    """Retrieve serves the full chatbot row via the same dynamic serializer as the export resource,
    so ``id`` is the integer pk and the UUID is its own ``public_id`` field."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)
    response = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 200
    body = response.json()
    assert body["public_id"] == str(experiment.public_id)
    assert body["name"] == experiment.name


@pytest.mark.django_db()
def test_retrieve_only_returns_working_versions(experiment):
    """Published versions aren't addressable here; only the working (top-level) chatbot is."""
    user = experiment.team.members.first()
    version = experiment.create_new_version()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": version.public_id}))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_chatbot_other_team_404(experiment):
    other_team = TeamWithUsersFactory.create()
    other_user = other_team.members.first()
    client = ApiTestClient(other_user, other_team)
    response = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_chatbot_anonymous_401(experiment):
    response = APIClient().get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 401
