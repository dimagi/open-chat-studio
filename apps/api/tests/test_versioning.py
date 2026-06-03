import pytest
from django.urls import resolve, reverse
from rest_framework.exceptions import NotFound
from rest_framework.test import APIRequestFactory

from apps.api.versioning import URLPathVersioning
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    exp = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    LlmProviderFactory.create(team=exp.team)
    return exp


class TestURLPathVersioning:
    """The version is read from the request path, defaulting to v1 for the unversioned alias."""

    def setup_method(self):
        self.factory = APIRequestFactory()
        self.versioning = URLPathVersioning()

    def test_unversioned_path_defaults_to_v1(self):
        request = self.factory.get("/api/experiments/")
        assert self.versioning.determine_version(request) == "v1"

    def test_explicit_v1_path(self):
        request = self.factory.get("/api/v1/experiments/")
        assert self.versioning.determine_version(request) == "v1"

    def test_disallowed_version_raises_not_found(self):
        request = self.factory.get("/api/v9/experiments/")
        with pytest.raises(NotFound):
            self.versioning.determine_version(request)


def test_reverse_produces_unversioned_urls():
    """reverse() must yield the canonical unversioned URLs so existing callers never break."""
    assert reverse("api:experiment-list") == "/api/experiments/"
    assert reverse("api:participant-data") == "/api/participants"


def test_v1_prefix_and_alias_resolve_to_same_view():
    """Both /api/v1/ and the unversioned alias forward-resolve to the same view."""
    assert resolve("/api/experiments/").func.__name__ == resolve("/api/v1/experiments/").func.__name__


@pytest.mark.django_db()
def test_unversioned_alias_serves_v1(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get("/api/experiments/")
    assert response.status_code == 200


@pytest.mark.django_db()
def test_v1_prefix_serves_same_surface_as_alias(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    versioned = client.get("/api/v1/experiments/")
    alias = client.get("/api/experiments/")
    assert versioned.status_code == 200
    assert versioned.json() == alias.json()


@pytest.mark.django_db()
def test_v2_not_yet_available(experiment):
    """v2 is allowed by settings but has no routes yet, so it 404s."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get("/api/v2/experiments/")
    assert response.status_code == 404
