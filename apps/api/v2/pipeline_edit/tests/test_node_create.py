"""POST /api/v2/chatbots/{id}/pipeline/nodes/ (#4140, spec §6.2).

The rule the refusals encode: a structurally-sound node always persists, even when it is
semantically incomplete, so an agent can build a graph a piece at a time. What does *not* persist is
a request naming something that does not exist -- a node type, or a resource id.

Resource ids are checked the same way whichever verb sent them, so that half is in
`test_param_values.py`; the whole-payload-per-type writes are in `test_full_payloads.py`.
"""

from unittest.mock import Mock, patch

import pytest

from apps.pipelines.models import Node

from .conftest import add_edge, add_llm_node, nodes_url, stored_node_params


@pytest.mark.django_db()
class TestNodeId:
    """Ids are the server's to assign (W5), drawn from a uuid and prefixed with the node's type."""

    def test_create_adds_a_node_with_a_server_assigned_id(self, client, chatbot, llm):
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

    def test_a_client_supplied_node_id_is_refused(self, client, chatbot):
        """Honouring a client's id would let two nodes collide."""
        response = client.post(
            nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "node_id": "mine-1"}, format="json"
        )

        assert response.status_code == 400, response.content
        assert "server" in str(response.json()["node_id"]).lower()
        assert not Node.objects.filter(pipeline=chatbot.pipeline, flow_id="mine-1").exists()

    def test_a_node_id_inside_params_is_dropped(self, client, chatbot):
        """`node_id` and `django_node` are the node model's own internals rather than params, so a
        client naming one is dropped the same way any other non-param is.

        The body-level rule cannot reach them: it guards the top-level keys, and `params` is a
        free-form object. Sent on a node that parses they are dumped back out again, so the spelling
        that matters is one that fails to parse -- `route_key` is missing here -- because that path
        stores what the client sent.
        """
        response = client.post(
            nodes_url(chatbot),
            {"type": "StaticRouterNode", "params": {"node_id": "hijack", "django_node": "x", "keywords": ["A", "B"]}},
            format="json",
        )

        assert response.status_code == 201, response.content
        stored = stored_node_params(chatbot, response.json()["node"]["node_id"])
        assert "node_id" not in stored
        assert "django_node" not in stored

    def test_a_node_id_inside_params_cannot_wedge_the_pipeline(self, client, chatbot, llm):
        """Why dropping them matters: stored, they collide with the keyword arguments
        `Node.pipeline_node_instance` passes, and `_output_map_for` catches only the two ways a node
        declines its params -- so the `TypeError` escapes `Pipeline.validate` itself.

        Asking a router for its output map is what reaches that call, so the router needs an
        outgoing edge to be asked. From there every read *and* every façade write of the pipeline
        raises, leaving nothing that could undo it.
        """
        created = client.post(
            nodes_url(chatbot),
            {"type": "StaticRouterNode", "params": {"node_id": "hijack", "keywords": ["A", "B"]}},
            format="json",
        )
        router = created.json()["node"]["node_id"]
        add_edge(chatbot.pipeline, chatbot.pipeline.node_set.get(type="StartNode").flow_id, router)
        add_edge(chatbot.pipeline, router, add_llm_node(client, chatbot, llm), source_handle="output_0")

        response = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/")

        assert response.status_code == 200, response.content

    def test_a_colliding_node_id_is_redrawn(self, client, chatbot):
        """``apply_pipeline_patch`` treats an add whose id already exists as a no-op, so a clash would
        answer 201 while describing the node that was already there."""
        created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
        taken = created.json()["node"]["node_id"]
        collision = taken.removeprefix("CodeNode-")

        # The first draw repeats the id already in the graph, the second is free.
        draws = [Mock(hex=f"{collision}0000000"), Mock(hex="abcde" + "0" * 27)]
        with patch("apps.api.v2.pipeline_edit.ids.uuid4", side_effect=draws):
            response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["node"]["node_id"] == "CodeNode-abcde"
        assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2

    def test_an_id_source_that_only_collides_still_answers(self, client, chatbot):
        """The redraw is bounded: an id source stuck on one value must fall back to a longer id rather
        than spin inside the pipeline row lock until the request times out."""
        created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
        collision = created.json()["node"]["node_id"].removeprefix("CodeNode-")

        with patch("apps.api.v2.pipeline_edit.ids.uuid4", return_value=Mock(hex=f"{collision}{'0' * 27}")):
            response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["node"]["node_id"] == f"CodeNode-{collision}{'0' * 27}"
        assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2


@pytest.mark.django_db()
class TestDefaultsAndLabel:
    def test_create_fills_in_the_node_types_defaults(self, client, chatbot):
        """`type` alone has to be enough to add a node.

        The node class is the only place the defaults live, and `update_nodes_from_data` stores params
        verbatim, so unless they are materialized here the node reads back from /inspect/ as the
        handful of keys the client happened to send. `name` is required on every type and has no
        default, so the server supplies the node id, as the UI builder does; the label defaults to the
        type's own.
        """
        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        assert response.status_code == 201, response.content
        node_id = response.json()["node"]["node_id"]
        node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
        assert node.params["history_type"] == "global"
        assert node.params["max_history_length"] == 10
        assert node.params["name"] == node_id
        assert response.json()["node"]["label"] == "LLM"

    def test_create_takes_a_label_and_a_name_over_the_defaults(self, client, chatbot):
        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "label": "Classify", "params": {"name": "classifier"}},
            format="json",
        )

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["node"]["label"] == "Classify"
        assert (
            Node.objects.get(pipeline=chatbot.pipeline, flow_id=body["node"]["node_id"]).params["name"] == "classifier"
        )


@pytest.mark.django_db()
class TestCanvasLayout:
    """Where a new node lands, and what that does to the output node.

    Nothing wires a new node yet, so there is no source to place it beside; it is parked a node's
    width right of every node already on the canvas, and the output is kept to the right of that.
    """

    def test_create_parks_the_node_clear_of_the_existing_ones(self, client, chatbot):
        chatbot.pipeline.node_set.update(position_x=400, position_y=50)

        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
        assert (node.position_x, node.position_y) == (800, 200)

    def test_create_leaves_the_output_node_where_it_is_when_the_new_node_lands_short_of_it(self, client, chatbot):
        """A layout someone arranged in the UI builder is not rearranged for the sake of it: a new
        node that fits to the output's left leaves it alone."""
        _place(chatbot.pipeline, start=100, end=800)

        client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        end = _end_node(chatbot.pipeline)
        assert (end.position_x, end.position_y) == (800, 200)

    def test_create_moves_the_output_node_clear_of_a_new_node_that_overtakes_it(self, client, chatbot):
        """A node level with or past the output would read as running after the end of the pipeline,
        so the output is moved a node's width beyond it: it is always the last node in x."""
        _place(chatbot.pipeline, start=100, end=300)

        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
        end = _end_node(chatbot.pipeline)
        assert (node.position_x, end.position_x, end.position_y) == (500, 900, 200)

    def test_create_moves_the_output_node_without_rewriting_what_it_holds(self, client, chatbot):
        """Moving the output means writing its row, and the graph's copy of a node's params carries
        the resource-id mirror `to_flow_node` merges in -- which the move must not store on the row."""
        _place(chatbot.pipeline, start=100, end=300)

        client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        end = _end_node(chatbot.pipeline)
        assert end.params == {"name": "end"}
        assert end.label == ""


@pytest.mark.django_db()
class TestRefusedBodies:
    """What the request envelope will not carry. An unknown key is refused where an unknown *param*
    is dropped: the body's own shape is the API's, while params belong to the node type's schema."""

    def test_an_unknown_node_type_is_refused(self, client, chatbot):
        """404, the same answer /pipeline/nodes/{type}/ gives, with the valid types alongside it."""
        response = client.post(nodes_url(chatbot), {"type": "Frobnicator"}, format="json")

        assert response.status_code == 404, response.content
        assert "LLMResponseWithPrompt" in response.json()["valid_types"]
        assert not chatbot.pipeline.node_set.filter(type="Frobnicator").exists()

    def test_a_server_managed_node_type_is_refused(self, client, chatbot):
        """Start and End are created with the pipeline and are not something a client may add -- the
        same refusal /pipeline/nodes/{type}/ already gives for them."""
        response = client.post(nodes_url(chatbot), {"type": "StartNode"}, format="json")

        assert response.status_code == 404, response.content
        assert "managed by the server" in response.json()["detail"]

    def test_a_body_with_no_type_is_refused(self, client, chatbot):
        response = client.post(nodes_url(chatbot), {"params": {"name": "orphan"}}, format="json")

        assert response.status_code == 400, response.content
        assert "type" in response.json()

    def test_an_unrecognised_body_key_is_refused(self, client, chatbot):
        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "colour": "red"}, format="json")

        assert response.status_code == 400, response.content
        assert "colour" in response.json()

    def test_an_unrecognised_param_is_dropped(self, client, chatbot):
        response = client.post(
            nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"tempreture": 0.5}}, format="json"
        )

        assert response.status_code == 201, response.content
        node_id = response.json()["node"]["node_id"]
        assert "tempreture" not in response.json()["node"]["params"]
        assert "tempreture" not in Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params


@pytest.mark.django_db()
class TestReportedRatherThanRefused:
    """Lenient on structure: a node that is merely incomplete lands anyway, and the gap shows up in
    the errors report the publish gate rejects on."""

    def test_a_missing_required_param_persists_and_is_reported(self, client, chatbot):
        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        assert response.status_code == 201, response.content
        body = response.json()
        node_id = body["node"]["node_id"]
        assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()
        assert body["pipeline_valid"] is False
        assert "llm_provider_id" in body["pipeline_errors"]["node"][node_id]

    def test_a_duplicate_node_name_persists_and_is_reported(self, client, chatbot, llm):
        """`name` is how one node reaches another's output, so a clash breaks the pipeline -- but it
        is structural, so it is reported rather than refused."""
        provider, model = llm
        params = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id, "name": "classifier"}

        first = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")
        second = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")

        assert (first.status_code, second.status_code) == (201, 201), second.content
        clashing = second.json()["pipeline_errors"]["node"]
        assert [error["name"] for error in clashing.values()] == ["All node names must be unique"] * 2


def _place(pipeline, start: int, end: int) -> None:
    """Give the start and end nodes an x each: the factory leaves positions null, so a test about
    layout has to supply them."""
    pipeline.node_set.filter(type="StartNode").update(position_x=start, position_y=200)
    pipeline.node_set.filter(type="EndNode").update(position_x=end, position_y=200)


def _end_node(pipeline) -> Node:
    return Node.objects.get(pipeline=pipeline, type="EndNode")
