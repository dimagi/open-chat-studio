"""DELETE /api/v2/chatbots/{id}/pipeline/edges/{edge_id}/ (#4141).

Unwiring is addressed by the edge id ``GET /chatbots/{id}/inspect/`` emits, which is also the id a
wire returns. Nothing about an edge is editable in place -- to move one end, delete it and wire a new
one -- so this endpoint is half of every rewiring an agent does.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.experiment import ChatbotFactory

from .conftest import add_edge, add_llm_node, add_router_node, boundary_node, edge_url, stored_edges, wire


@pytest.fixture()
def spliced(client, chatbot, llm, start, end):
    """An LLM node wired between Start and End, with the direct Start -> End edge gone.

    Spliced rather than added alongside: with the direct edge still there, unwiring this node would
    leave a perfectly valid graph and prove nothing.
    """
    node_id = add_llm_node(client, chatbot, llm)
    chatbot.pipeline.data["edges"] = []
    chatbot.pipeline.save(update_fields=["data"])
    return node_id, wire(client, chatbot, start, node_id), wire(client, chatbot, node_id, end)


@pytest.mark.django_db()
def test_delete_removes_only_the_edge_addressed(client, chatbot, spliced):
    _node_id, incoming, outgoing = spliced

    response = client.delete(edge_url(chatbot, outgoing))

    assert response.status_code == 200, response.content
    assert [edge["id"] for edge in stored_edges(chatbot.pipeline)] == [incoming]


@pytest.mark.django_db()
def test_delete_leaves_the_nodes_the_edge_joined_in_place(client, chatbot, spliced):
    """Unwiring is not deleting: both nodes stay, and stay where they were on the canvas."""
    node_id, _incoming, outgoing = spliced
    before = {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()}

    client.delete(edge_url(chatbot, outgoing))

    chatbot.pipeline.refresh_from_db()
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()
    assert {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()} == before


@pytest.mark.django_db()
def test_delete_leaves_the_other_node_rows_alone(client, chatbot, spliced):
    """``update_nodes_from_data`` reads its mapping as the whole graph membership, so an edge-only
    diff has to hand it every node -- naming none would reconcile them all away."""
    _node_id, _incoming, outgoing = spliced
    before = set(chatbot.pipeline.node_set.values_list("flow_id", "type", "label"))

    client.delete(edge_url(chatbot, outgoing))

    chatbot.pipeline.refresh_from_db()
    assert set(chatbot.pipeline.node_set.values_list("flow_id", "type", "label")) == before


@pytest.mark.django_db()
def test_delete_reports_the_hole_it_leaves(client, chatbot, spliced, end):
    """Lenient on structure: unwiring usually breaks the path to End, which is reported rather than
    refused so the agent can wire a replacement in. No ``edge`` key -- there is no edge left to
    describe, the same as a node delete carries no ``node``."""
    _node_id, _incoming, outgoing = spliced

    body = client.delete(edge_url(chatbot, outgoing)).json()

    assert body["pipeline_valid"] is False
    assert "not reachable" in body["pipeline_errors"]["node"][end]["root"]
    assert "edge" not in body


@pytest.mark.django_db()
def test_delete_puts_the_handles_back_on_the_unwired_list(client, chatbot, spliced, end):
    """The mirror of what a wire does: both ends of the edge come back onto the map an agent works
    down to finish a graph."""
    node_id, _incoming, outgoing = spliced

    body = client.delete(edge_url(chatbot, outgoing)).json()

    assert body["unwired_handles"][node_id] == [{"handle": "output", "label": None}]
    assert body["unwired_handles"][end] == [{"handle": "input", "label": None}]


@pytest.mark.django_db()
def test_delete_bumps_the_edit_revision(client, chatbot, spliced):
    """An open UI builder session has to see this as a conflict rather than overwrite it."""
    _node_id, _incoming, outgoing = spliced
    chatbot.pipeline.refresh_from_db()
    before = chatbot.pipeline.edit_revision

    client.delete(edge_url(chatbot, outgoing))

    chatbot.pipeline.refresh_from_db()
    assert chatbot.pipeline.edit_revision == before + 1


@pytest.mark.django_db()
def test_delete_of_an_unknown_edge_is_a_404(client, chatbot):
    """An edge id is an address, so a wrong one is a wrong URL rather than a bad body."""
    response = client.delete(edge_url(chatbot, "reactflow__edge-nope"))

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_a_repeated_delete_is_a_404_and_changes_nothing(client, chatbot, spliced):
    """What a retry of an unwire looks like: the second call reports the edge already gone rather than
    disturbing the graph the first call left."""
    _node_id, incoming, outgoing = spliced
    assert client.delete(edge_url(chatbot, outgoing)).status_code == 200

    response = client.delete(edge_url(chatbot, outgoing))

    assert response.status_code == 404, response.content
    assert [edge["id"] for edge in stored_edges(chatbot.pipeline)] == [incoming]


@pytest.mark.django_db()
def test_an_edge_belonging_to_another_chatbot_is_a_404(client, chatbot, llm):
    """Edge ids are unique per pipeline rather than globally, so the id has to be looked for in *this*
    chatbot's graph."""
    elsewhere = ChatbotFactory.create(team=chatbot.team, name="Other bot", description="")
    other_edge = wire(client, elsewhere, add_llm_node(client, elsewhere, llm), boundary_node(elsewhere, "EndNode"))

    response = client.delete(edge_url(chatbot, other_edge))

    assert response.status_code == 404, response.content
    assert other_edge in {edge["id"] for edge in stored_edges(elsewhere.pipeline)}


@pytest.mark.django_db()
def test_a_stranded_edge_can_be_deleted(client, chatbot, llm, start, end):
    """The only way to clear ``pipeline_errors.edge``: an edge on a handle its source does not offer
    cannot be created through this API, but a graph edited in the UI builder can hold one."""
    node_id = add_llm_node(client, chatbot, llm)
    add_edge(chatbot.pipeline, start, node_id)
    stranded = add_edge(chatbot.pipeline, node_id, end, source_handle="output_7")
    inspected = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()
    assert inspected["pipeline_errors"]["edge"] == [stranded]

    body = client.delete(edge_url(chatbot, stranded)).json()

    assert body["pipeline_errors"]["edge"] == []
    assert stranded not in {edge["id"] for edge in stored_edges(chatbot.pipeline)}


@pytest.mark.django_db()
def test_an_edge_deleted_and_wired_again_comes_back_with_the_same_id(client, chatbot, spliced, end):
    """The id is derived from the endpoints, so re-wiring the same pair reuses it. Worth pinning: it
    is what lets a client that lost track of an unwire recognise the edge it re-created."""
    node_id, _incoming, outgoing = spliced
    client.delete(edge_url(chatbot, outgoing))

    assert wire(client, chatbot, node_id, end) == outgoing


@pytest.mark.django_db()
def test_a_router_branch_is_repointed_by_unwiring_and_wiring_again(client, chatbot, llm, end):
    """The route an agent takes to change where a branch goes, since an edge is not editable in
    place."""
    router = add_router_node(client, chatbot, llm)
    other = add_llm_node(client, chatbot, llm)
    first = wire(client, chatbot, router, end, source_handle="output_0")

    client.delete(edge_url(chatbot, first))
    second = wire(client, chatbot, router, other, source_handle="output_0")

    assert [(edge["id"], edge["target"]) for edge in stored_edges(chatbot.pipeline) if edge["source"] == router] == [
        (second, other)
    ]
