"""A write response is a body you can send straight back (#4140).

``WrittenNodeSerializer`` promises the shape you can PATCH again, and an agent's ordinary loop is
read-modify-write. So for every served type: create it, send its own reported params back, and
expect that to be accepted rather than argued with.
"""

import pytest

from apps.api.v2.discovery.node_types import get_node_types
from apps.pipelines.models import Node

from .conftest import node_url, nodes_url

SERVED_TYPES = [node_type["type"] for node_type in get_node_types()]


@pytest.mark.django_db()
@pytest.mark.parametrize("node_type", SERVED_TYPES, ids=SERVED_TYPES)
def test_a_write_response_is_a_valid_request_body(client, chatbot, node_type):
    created = client.post(nodes_url(chatbot), {"type": node_type}, format="json")
    assert created.status_code == 201, created.content
    node = created.json()["node"]

    echoed = client.patch(node_url(chatbot, node["node_id"]), {"params": node["params"]}, format="json")

    assert echoed.status_code == 200, echoed.content


@pytest.mark.django_db()
def test_a_label_only_edit_does_not_rewrite_params(client, chatbot):
    """The graph a PATCH reads back merges the resource-id mirror into every type's params
    (``Node.to_flow_node``). Merging that into the row would store seven keys ``CodeNode`` does not
    declare, on an edit that never mentioned params."""
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    node_id = created.json()["node"]["node_id"]
    before = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params

    response = client.patch(node_url(chatbot, node_id), {"label": "Renamed"}, format="json")

    assert response.status_code == 200, response.content
    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
    assert node.label == "Renamed"
    assert node.params == before


@pytest.mark.django_db()
def test_a_withheld_param_is_not_reported_as_settable(client, chatbot):
    """``mcp_tools`` is ``api:exclude``d, so PATCH refuses it -- which makes reporting it in the
    response a body the caller cannot send back."""
    created = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert "mcp_tools" not in created.json()["node"]["params"]


@pytest.mark.django_db()
def test_params_are_written_through_the_patch_engine_only(client, chatbot):
    """The blob is the source of truth (#4140): the row is written by ``update_nodes_from_data``
    off the patched graph, never by the view reaching for ``Node`` directly. A row whose params
    match the graph's is the observable form of that."""
    created = client.post(
        nodes_url(chatbot),
        {"type": "CodeNode", "params": {"code": "def main(x, **k): return x"}},
        format="json",
    )
    node_id = created.json()["node"]["node_id"]

    stored = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
    in_graph = next(node for node in chatbot.pipeline.flow_data["nodes"] if node["id"] == node_id)

    assert stored.params["code"] == in_graph["data"]["params"]["code"]
    assert stored.params["code"] == "def main(x, **k): return x"
