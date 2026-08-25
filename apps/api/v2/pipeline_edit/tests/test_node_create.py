"""POST /api/v2/chatbots/{id}/pipeline/nodes/ (#4140)."""

import pytest

from apps.pipelines.models import Node

from .conftest import nodes_url


@pytest.mark.django_db()
def test_create_adds_a_node_with_a_server_assigned_id(client, chatbot, llm):
    provider, model = llm

    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    node_id = response.json()["node"]["node_id"]
    assert node_id.startswith("LLMResponseWithPrompt-")
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()


@pytest.mark.django_db()
def test_create_persists_the_node_types_defaults(client, chatbot):
    """The node class is the only place the defaults live, and `update_nodes_from_data` stores
    params verbatim -- so unless they are materialized here, a node created through the API reads
    back from /inspect/ as the handful of keys the client happened to send."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 201, response.content
    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
    assert node.params["history_type"] == "global"
    assert node.params["max_history_length"] == 10


@pytest.mark.django_db()
def test_create_names_the_node_after_its_id(client, chatbot):
    """`name` is required on every node type and has no default, so the server has to supply one.
    It uses the node id, which is what the builder writes when a node is dragged onto the canvas."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    node_id = response.json()["node"]["node_id"]
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["name"] == node_id


@pytest.mark.django_db()
def test_create_accepts_a_name_over_the_default(client, chatbot):
    response = client.post(
        nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"name": "classifier"}}, format="json"
    )

    node_id = response.json()["node"]["node_id"]
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["name"] == "classifier"


@pytest.mark.django_db()
def test_create_labels_the_node_with_its_types_display_name(client, chatbot):
    """The builder shows a node's label, so a node created without one has to arrive with
    something readable rather than blank."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.json()["node"]["label"] == "LLM"


@pytest.mark.django_db()
def test_create_accepts_a_label(client, chatbot):
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "label": "Classify"}, format="json")

    assert response.json()["node"]["label"] == "Classify"


@pytest.mark.django_db()
def test_create_parks_the_node_clear_of_the_existing_ones(client, chatbot):
    """Nothing wires a new node yet, so there is no source to place it beside; it is parked a node's
    width right of every node already on the canvas -- bar the output, which the next tests cover."""
    chatbot.pipeline.node_set.update(position_x=400, position_y=50)

    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
    assert (node.position_x, node.position_y) == (800, 200)


@pytest.mark.django_db()
def test_create_leaves_the_output_node_where_it_is_when_the_new_node_lands_short_of_it(client, chatbot):
    """A layout someone arranged in the builder is not rearranged for the sake of it: a new node
    that fits to the output's left leaves it alone."""
    _place(chatbot.pipeline, start=100, end=800)

    client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    end = _end_node(chatbot.pipeline)
    assert (end.position_x, end.position_y) == (800, 200)


@pytest.mark.django_db()
def test_create_moves_the_output_node_clear_of_a_new_node_that_overtakes_it(client, chatbot):
    """A node level with or past the output would read as running after the end of the pipeline, so
    the output is moved a node's width beyond it: it is always the last node in the x direction."""
    _place(chatbot.pipeline, start=100, end=300)

    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
    end = _end_node(chatbot.pipeline)
    assert (node.position_x, end.position_x, end.position_y) == (500, 900, 200)


@pytest.mark.django_db()
def test_create_moves_the_output_node_without_rewriting_what_it_holds(client, chatbot):
    """Moving the output means writing its row, and the graph's copy of a node's params carries the
    resource-id mirror `to_flow_node` merges in -- which the move must not store on the row."""
    _place(chatbot.pipeline, start=100, end=300)

    client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    end = _end_node(chatbot.pipeline)
    assert end.params == {"name": "end"}
    assert end.label == ""


def _place(pipeline, start: int, end: int) -> None:
    """Give the start and end nodes an x each: the factory leaves positions null, so a test about
    layout has to supply them."""
    pipeline.node_set.filter(type="StartNode").update(position_x=start, position_y=200)
    pipeline.node_set.filter(type="EndNode").update(position_x=end, position_y=200)


def _end_node(pipeline) -> Node:
    return Node.objects.get(pipeline=pipeline, type="EndNode")
