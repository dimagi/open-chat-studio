import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


def _get_client_for_user(user, team):
    client = APIClient()
    _user_key, api_key = UserAPIKey.objects.create_key(name="Test", user=user, team=team)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {api_key}")
    return client


@pytest.mark.django_db()
def test_list_experiments(experiment):
    user = experiment.team.members.first()
    client = _get_client_for_user(user, experiment.team)
    response = client.get(reverse("api:list-experiments"))
    assert response.status_code == 200
    expected_json = [{"name": experiment.name, "experiment_id": experiment.public_id}]
    assert response.json() == expected_json
