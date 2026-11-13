from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.api.v2.serializers import ChatbotSerializer
from apps.api.v2.views import ChatbotViewSet
from apps.api.v2.write.serializers import ChatbotCreateSerializer, ChatbotWriteSerializer
from apps.channels.models import ChannelPlatform
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
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


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        pytest.param("list", ChatbotSerializer, id="list"),
        pytest.param("retrieve", ChatbotSerializer, id="retrieve"),
        pytest.param("create", ChatbotCreateSerializer, id="create"),
        pytest.param("partial_update", ChatbotWriteSerializer, id="partial-update"),
        pytest.param("inspect", ChatbotInspectSerializer, id="inspect"),
    ],
)
def test_get_serializer_class_matches_the_action(action, expected):
    """Three of the five actions build their serializer directly, so this method is easy to leave
    behind -- and OPTIONS metadata reads it."""
    view = ChatbotViewSet()
    view.action = action

    assert view.get_serializer_class() is expected


@pytest.mark.django_db()
def test_v2_experiments_still_404(experiment):
    """v2 uses /chatbots/; the old /experiments/ path must not exist under v2."""
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
def test_read_only_key_can_read_chatbots(experiment):
    """The chatbots endpoints are read-only, so a read-only key keeps full access (ADR-0021)."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team, read_only=True)

    assert client.get(reverse("api:v2:chatbot-list")).status_code == 200
    assert client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id})).status_code == 200


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


def _machine_client(team, **reach):
    """A machine (client-credentials) client holding chatbots:read; `reach` is its allowlist or all_data."""
    return ApiTestClient(
        team.members.first(), team, auth_method="oauth_client_credentials", scopes=["chatbots:read"], **reach
    )


@pytest.mark.django_db()
def test_a_limited_machine_token_lists_only_its_allowed_chatbots(experiment):
    """A list has no one chatbot to refuse, so the unlisted ones are left out of it (ADR-0056)."""
    ExperimentFactory.create(team=experiment.team)
    client = _machine_client(experiment.team, allowed_chatbots=[experiment])

    results = client.get(reverse("api:v2:chatbot-list")).json()["results"]

    assert [result["id"] for result in results] == [str(experiment.public_id)]


@pytest.mark.django_db()
def test_a_machine_token_reaching_all_data_lists_every_chatbot(experiment):
    other = ExperimentFactory.create(team=experiment.team)
    client = _machine_client(experiment.team, all_data=True)

    results = client.get(reverse("api:v2:chatbot-list")).json()["results"]

    assert {result["id"] for result in results} == {str(experiment.public_id), str(other.public_id)}


def _detail_url(experiment):
    return reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id})


def _inspect_url(experiment, version=None):
    url = reverse("api:v2:chatbot-inspect", kwargs={"id": experiment.public_id})
    return f"{url}?version={version}" if version else url


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "url_for",
    [
        pytest.param(_detail_url, id="retrieve"),
        pytest.param(_inspect_url, id="inspect-working"),
        pytest.param(lambda experiment: _inspect_url(experiment, version=1), id="inspect-version"),
    ],
)
@pytest.mark.parametrize(
    "listed", [pytest.param(False, id="unlisted-is-refused"), pytest.param(True, id="listed-may-read")]
)
def test_a_machine_token_reads_only_its_allowed_chatbots(experiment, url_for, listed):
    """Addressed directly, an unlisted chatbot is refused rather than hidden, as every write refuses it."""
    experiment.create_new_version()
    client = _machine_client(experiment.team, allowed_chatbots=[experiment] if listed else [])

    response = client.get(url_for(experiment))

    assert response.status_code == (200 if listed else 403), response.content
    if not listed:
        assert response.json() == {"detail": "This application is not authorized to access this chatbot."}


EARLIER = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 2, 1, tzinfo=UTC)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("sessions", "expected"),
    [
        pytest.param([], None, id="no-sessions"),
        pytest.param([(ChannelPlatform.WEB, None)], None, id="session-without-activity"),
        pytest.param(
            [(ChannelPlatform.WEB, EARLIER), (ChannelPlatform.API, LATER), (ChannelPlatform.WEB, None)],
            LATER,
            id="latest-across-sessions",
        ),
        pytest.param(
            [(ChannelPlatform.WEB, EARLIER), (ChannelPlatform.EVALUATIONS, LATER)],
            EARLIER,
            id="evaluation-sessions-ignored",
        ),
    ],
)
def test_chatbot_last_activity_at(experiment, sessions, expected):
    for platform, last_activity_at in sessions:
        ExperimentSessionFactory.create(
            experiment=experiment, experiment_channel=None, platform=platform, last_activity_at=last_activity_at
        )
    client = ApiTestClient(experiment.team.members.first(), experiment.team)

    listed = client.get(reverse("api:v2:chatbot-list")).json()["results"][0]
    retrieved = client.get(reverse("api:v2:chatbot-detail", kwargs={"id": experiment.public_id})).json()

    for payload in (listed, retrieved):
        value = payload["last_activity_at"]
        assert (parse_datetime(value) if value else None) == expected


@pytest.mark.django_db()
def test_list_chatbots_query_count_does_not_grow_with_chatbots(experiment):
    client = ApiTestClient(experiment.team.members.first(), experiment.team)
    ExperimentSessionFactory.create(
        experiment=experiment, experiment_channel=None, platform=ChannelPlatform.WEB, last_activity_at=EARLIER
    )
    url = reverse("api:v2:chatbot-list")
    with CaptureQueriesContext(connection) as one_chatbot:
        client.get(url)

    for chatbot in ExperimentFactory.create_batch(3, team=experiment.team):
        ExperimentSessionFactory.create(
            experiment=chatbot, experiment_channel=None, platform=ChannelPlatform.WEB, last_activity_at=LATER
        )
    with CaptureQueriesContext(connection) as four_chatbots:
        response = client.get(url)

    assert len(response.json()["results"]) == 4
    assert len(four_chatbots) == len(one_chatbot)
