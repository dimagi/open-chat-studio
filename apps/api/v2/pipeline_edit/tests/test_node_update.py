"""PATCH /api/v2/chatbots/{id}/pipeline/nodes/{node_id}/ (#4140).

What an edit touches and what it leaves alone.
"""

import pytest

from apps.pipelines.models import Node

from .conftest import add_llm_node, node_url, nodes_url


@pytest.fixture()
def llm_node(client, chatbot, llm):
    return add_llm_node(client, chatbot, llm)


@pytest.mark.django_db()
class TestParamMerge:
    """Only what the body named is written."""

    def test_patch_merges_into_the_stored_params(self, client, chatbot, llm_node):
        """Only the params sent are touched: a whole-params replace would make editing one field mean
        resending the node, which is what the façade exists to avoid."""
        response = client.patch(node_url(chatbot, llm_node), {"params": {"prompt": "Be terse."}}, format="json")

        assert response.status_code == 200, response.content
        params = Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).params
        assert params["prompt"] == "Be terse."
        assert params["max_history_length"] == 10
        assert params["llm_provider_id"] is not None

    def test_patch_updates_the_label(self, client, chatbot, llm_node):
        response = client.patch(node_url(chatbot, llm_node), {"label": "Classify"}, format="json")

        assert response.status_code == 200, response.content
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).label == "Classify"

    def test_patch_leaves_the_label_alone_when_it_is_not_sent(self, client, chatbot, llm_node):
        client.patch(node_url(chatbot, llm_node), {"label": "Classify"}, format="json")

        client.patch(node_url(chatbot, llm_node), {"params": {"prompt": "Be terse."}}, format="json")

        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).label == "Classify"

    def test_a_label_only_edit_does_not_rewrite_params(self, client, chatbot):
        """The graph a PATCH reads back merges the resource-id mirror into every type's params
        (``Node.to_flow_node``). Merging that into the row would store seven keys ``CodeNode`` does
        not declare, on an edit that never mentioned params."""
        created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
        node_id = created.json()["node"]["node_id"]
        before = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params

        response = client.patch(node_url(chatbot, node_id), {"label": "Renamed"}, format="json")

        assert response.status_code == 200, response.content
        node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
        assert node.label == "Renamed"
        assert node.params == before

    def test_patch_drops_a_param_the_type_does_not_declare(self, client, chatbot, llm_node):
        response = client.patch(node_url(chatbot, llm_node), {"params": {"tempreture": 0.5}}, format="json")

        assert response.status_code == 200, response.content
        assert "tempreture" not in response.json()["node"]["params"]
        assert "tempreture" not in Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).params


@pytest.mark.django_db()
class TestRefusals:
    def test_patch_of_an_unknown_node_is_a_404(self, client, chatbot):
        response = client.patch(node_url(chatbot, "LLMResponseWithPrompt-nope1"), {"label": "x"}, format="json")

        assert response.status_code == 404, response.content

    def test_patch_refuses_to_change_a_nodes_type(self, client, chatbot, llm_node):
        """A node's type decides what its params mean, so switching it in place would reinterpret
        every stored value. Delete the node and add the other type instead."""
        response = client.patch(node_url(chatbot, llm_node), {"type": "RouterNode"}, format="json")

        assert response.status_code == 400, response.content
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).type == "LLMResponseWithPrompt"

    @pytest.mark.parametrize("node_type", ["StartNode", "EndNode"])
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"label": "Begin here"}, id="label"),
            pytest.param({"params": {"name": "renamed"}}, id="params"),
        ],
    )
    def test_a_server_managed_node_cannot_be_edited(self, client, chatbot, node_type, body):
        """Start and End are the server's, whichever half of the body names them.

        409 rather than 404, and the same answer DELETE gives: the node is there and the address is
        right, so the refusal is about what the node is, not about where it was looked for."""
        node_id = chatbot.pipeline.node_set.get(type=node_type).flow_id
        before = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)

        response = client.patch(node_url(chatbot, node_id), body, format="json")

        assert response.status_code == 409, response.content
        assert "cannot be edited or deleted" in response.json()["detail"]
        after = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
        assert (after.label, after.params) == (before.label, before.params)


@pytest.mark.django_db()
class TestDeprecatedNodeTypes:
    """A type the API no longer publishes has no schema to describe or check, so a pipeline holding
    one stays editable only as far as it can be edited without one."""

    @pytest.fixture()
    def deprecated_node(self, chatbot):
        node = Node.objects.create(
            pipeline=chatbot.pipeline,
            type="LLMResponse",
            flow_id="LLMResponse-old1",
            label="Old",
            params={"name": "old"},
        )
        chatbot.pipeline.data["nodes"] = []
        chatbot.pipeline.save(update_fields=["data"])
        return node

    def test_a_label_only_edit_to_a_deprecated_type_is_allowed(self, client, chatbot, deprecated_node):
        """Renaming such a node needs no schema, and a pipeline holding one has to stay editable."""
        response = client.patch(node_url(chatbot, deprecated_node.flow_id), {"label": "Renamed"}, format="json")

        assert response.status_code == 200, response.content
        deprecated_node.refresh_from_db()
        assert deprecated_node.label == "Renamed"

    def test_setting_a_param_on_a_deprecated_type_is_refused(self, client, chatbot, deprecated_node):
        """The other half of it: the API has no schema to check the value against, so it will not
        pretend to."""
        response = client.patch(node_url(chatbot, deprecated_node.flow_id), {"params": {"name": "new"}}, format="json")

        assert response.status_code == 404, response.content
