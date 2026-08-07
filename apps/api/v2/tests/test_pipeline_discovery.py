import pytest
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


def test_pipeline_nodes_reverse_is_versioned():
    assert reverse("api:v2:pipeline-nodes") == "/api/v2/pipeline/nodes/"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_node_types(auth_method, team):
    client = ApiTestClient(team.members.first(), team, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-nodes"))

    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()}
    assert by_type["LLMResponseWithPrompt"]["description"]
    assert by_type["LLMResponseWithPrompt"]["schema"]["properties"]


@pytest.mark.django_db()
def test_deprecated_node_types_are_excluded(team):
    client = ApiTestClient(team.members.first(), team)
    types = {entry["type"] for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert {"AssistantNode", "BooleanNode", "LLMResponse"}.isdisjoint(types)


@pytest.mark.django_db()
def test_structural_node_types_are_listed_but_flagged_not_addable(team):
    """Start/End/Passthrough appear in every inspected graph, so the agent must be able to resolve
    them here -- but it must not create them."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type["StartNode"]["can_add"] is False
    assert by_type["EndNode"]["can_add"] is False
    assert by_type["Passthrough"]["can_add"] is False
    assert by_type["LLMResponseWithPrompt"]["can_add"] is True


@pytest.mark.django_db()
def test_ui_keys_are_stripped(team):
    client = ApiTestClient(team.members.first(), team)
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        assert not [key for key in entry["schema"] if key.startswith("ui:")], entry["type"]


@pytest.mark.django_db()
def test_type_filter_returns_a_single_element_array(team):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": "RouterNode"})

    assert response.status_code == 200
    assert [entry["type"] for entry in response.json()] == ["RouterNode"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("Frobnicator", id="unknown-type"),
        pytest.param("BooleanNode", id="deprecated-type-is-not-discoverable"),
    ],
)
def test_type_filter_404s(team, node_type):
    client = ApiTestClient(team.members.first(), team)
    assert client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type}).status_code == 404


@pytest.mark.django_db()
def test_unauthenticated_request_is_rejected(team, client):
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 401


@pytest.mark.django_db()
def test_read_only_api_key_may_read(team):
    """The inspect key is read-only; discovery is a GET, so it must work."""
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 200
