"""DELETE /api/v2/chatbots/{id}/pipeline/nodes/{node_id}/ (#4140)."""

import pytest

from apps.pipelines.models import Node

from .conftest import add_edge, node_url, nodes_url


@pytest.fixture()
def wired_llm_node(client, chatbot, llm):
    """An LLM node spliced between Start and End, so deleting it has edges to take with it."""
    provider, model = llm
    node_id = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    ).json()["node"]["node_id"]
    start = chatbot.pipeline.node_set.get(type="StartNode").flow_id
    end = chatbot.pipeline.node_set.get(type="EndNode").flow_id
    # Spliced, not added alongside: with the direct Start -> End edge still there, removing this
    # node would leave a perfectly valid graph and prove nothing.
    chatbot.pipeline.data["edges"] = []
    add_edge(chatbot.pipeline, start, node_id)
    add_edge(chatbot.pipeline, node_id, end)
    return node_id


@pytest.mark.django_db()
def test_delete_removes_the_node_and_its_edges(client, chatbot, wired_llm_node):
    """An edge left pointing at a node that no longer exists breaks cycle detection and
    reachability, so culling them is the server's job and not something the caller has to ask for."""
    response = client.delete(node_url(chatbot, wired_llm_node))

    assert response.status_code == 200, response.content
    assert not Node.objects.filter(pipeline=chatbot.pipeline, flow_id=wired_llm_node).exists()
    chatbot.pipeline.refresh_from_db()
    endpoints = {end for edge in chatbot.pipeline.data["edges"] for end in (edge["source"], edge["target"])}
    assert wired_llm_node not in endpoints


@pytest.mark.django_db()
def test_delete_reports_the_hole_it_leaves(client, chatbot, wired_llm_node):
    """Lenient on structure: unsplicing a node breaks the path to End, which is reported rather
    than refused so the agent can wire a replacement in."""
    body = client.delete(node_url(chatbot, wired_llm_node)).json()

    assert body["pipeline_valid"] is False
    assert "node" not in body


@pytest.mark.django_db()
def test_delete_of_an_unknown_node_is_a_404(client, chatbot):
    response = client.delete(node_url(chatbot, "LLMResponseWithPrompt-nope1"))

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("node_type", ["StartNode", "EndNode"])
def test_delete_refuses_a_node_the_server_manages(client, chatbot, node_type):
    """Start and End cannot be added back through the API — POST refuses those types — so a delete
    would strand the chatbot in a state only the builder could repair."""
    node_id = chatbot.pipeline.node_set.get(type=node_type).flow_id

    response = client.delete(node_url(chatbot, node_id))

    assert response.status_code == 409, response.content
    assert "cannot be edited or deleted" in response.json()["detail"]
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()


@pytest.mark.django_db()
def test_delete_allows_a_deprecated_node_type(client, chatbot):
    """A type that can no longer be added can still be removed — that is how a pipeline gets off
    a deprecated node."""
    node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="Passthrough-1", type="Passthrough", params={})

    response = client.delete(node_url(chatbot, node.flow_id))

    assert response.status_code == 200, response.content
    assert not Node.objects.filter(pipeline=chatbot.pipeline, flow_id="Passthrough-1").exists()
