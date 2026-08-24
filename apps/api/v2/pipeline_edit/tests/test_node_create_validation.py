"""What POST /pipeline/nodes/ refuses and what it accepts-and-reports (#4140, spec §6.2).

The rule the table below encodes: a structurally-sound node always persists, even when it is
semantically incomplete, so an agent can build a graph a piece at a time. What does *not* persist
is a request naming something that does not exist — a node type, or a resource id.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import nodes_url


@pytest.mark.django_db()
def test_an_unknown_node_type_is_refused(client, chatbot):
    """400, not 404: `type` is a field the client chose, so it is reported like any other bad
    field. A 404 here would mean "no such chatbot" to anything reading status codes alone."""
    response = client.post(nodes_url(chatbot), {"type": "Frobnicator"}, format="json")

    assert response.status_code == 400, response.content
    assert "LLMResponseWithPrompt" in response.json()["type"]["valid_types"]
    assert not chatbot.pipeline.node_set.filter(type="Frobnicator").exists()


@pytest.mark.django_db()
def test_a_server_managed_node_type_is_refused(client, chatbot):
    """Start and End are created with the pipeline and are not something a client may add — the
    same refusal /pipeline/nodes/{type}/ already gives for them."""
    response = client.post(nodes_url(chatbot), {"type": "StartNode"}, format="json")

    assert response.status_code == 400, response.content
    assert "managed by the server" in response.json()["type"]["detail"]


@pytest.mark.django_db()
def test_a_body_with_no_type_is_refused(client, chatbot):
    response = client.post(nodes_url(chatbot), {"params": {"name": "orphan"}}, format="json")

    assert response.status_code == 400, response.content
    assert "type" in response.json()


@pytest.mark.django_db()
def test_a_client_supplied_node_id_is_refused(client, chatbot):
    """Ids are the server's to assign (W5): honouring a client's would let two nodes collide."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "node_id": "mine-1"}, format="json")

    assert response.status_code == 400, response.content
    assert "server" in str(response.json()["node_id"]).lower()
    assert not Node.objects.filter(pipeline=chatbot.pipeline, flow_id="mine-1").exists()


@pytest.mark.django_db()
def test_an_unrecognised_body_key_is_refused(client, chatbot):
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "colour": "red"}, format="json")

    assert response.status_code == 400, response.content
    assert "colour" in response.json()


@pytest.mark.django_db()
def test_an_unrecognised_param_is_refused(client, chatbot):
    """A param the node type does not declare would be stored and then ignored at run time, which
    for an agent is a 201 that quietly did not do what it asked for."""
    response = client.post(
        nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"tempreture": 0.5}}, format="json"
    )

    assert response.status_code == 400, response.content
    assert "tempreture" in str(response.json())


@pytest.mark.django_db()
def test_a_missing_required_param_persists_and_is_reported(client, chatbot):
    """Lenient on structure: the node lands so the next call can fill it in, and the gap shows up
    in the errors report the publish gate rejects on."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    node_id = body["node"]["node_id"]
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()
    assert body["pipeline_valid"] is False
    assert "llm_provider_id" in body["pipeline_errors"]["node"][node_id]


@pytest.mark.django_db()
def test_a_param_of_the_wrong_type_is_refused(client, chatbot, llm):
    """A value the node type cannot parse is not a structural gap, so it is turned away rather than
    stored and reported. See ``test_node_param_types`` for why."""
    provider, model = llm

    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {
                "llm_provider_id": provider.id,
                "llm_provider_model_id": model.id,
                "max_history_length": "loads",
            },
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "max_history_length" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()


@pytest.mark.django_db()
def test_a_reference_to_a_nonexistent_resource_is_refused(client, chatbot):
    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": 9999}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "llm_provider_id" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()


@pytest.mark.django_db()
def test_a_reference_to_another_teams_resource_is_refused(client, chatbot):
    """Indistinguishable from a nonexistent id on purpose: telling the two apart would answer
    whether the id exists in some other team."""
    elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": elsewhere.id}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "llm_provider_id" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()


@pytest.mark.django_db()
def test_a_reference_to_the_teams_own_resource_is_accepted(client, chatbot, team):
    """Guards the guard: the refusals above have to be about the team boundary, not about the
    reference check refusing every id it is shown."""
    material = SourceMaterialFactory.create(team=team)

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"source_material_id": material.id}},
        format="json",
    )

    assert response.status_code == 201, response.content


@pytest.mark.django_db()
def test_a_duplicate_node_name_persists_and_is_reported(client, chatbot, llm):
    """`name` is how one node reaches another's output, so a clash breaks the pipeline -- but it is
    structural, so it is reported rather than refused."""
    provider, model = llm
    params = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id, "name": "classifier"}

    first = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")
    second = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")

    assert (first.status_code, second.status_code) == (201, 201), second.content
    clashing = second.json()["pipeline_errors"]["node"]
    assert [error["name"] for error in clashing.values()] == ["All node names must be unique"] * 2


@pytest.mark.django_db()
def test_a_list_valued_reference_is_checked_per_entry(client, chatbot, team):
    """`collection_index_ids` holds a list, so the check has to look at every entry rather than at
    the list as a whole."""
    ours = CollectionFactory.create(team=team, is_index=True)
    theirs = CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True)

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"collection_index_ids": [ours.id, theirs.id]}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert str(theirs.id) in response.json()["params"]["collection_index_ids"]
    assert str(ours.id) not in response.json()["params"]["collection_index_ids"]


@pytest.mark.django_db()
def test_a_malformed_custom_action_reference_is_refused(client, chatbot):
    """`custom_actions` entries are the composite "{action_id}:{operation_id}" strings the server
    hands out, and `Node.update_from_params` splits them on the colon -- so a value that is not one
    would be a 500 rather than a rejected write if it got as far as being saved."""
    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"custom_actions": ["not-a-reference"]}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "custom_actions" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()
