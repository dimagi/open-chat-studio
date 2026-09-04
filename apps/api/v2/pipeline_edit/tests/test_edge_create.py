"""POST /api/v2/chatbots/{id}/pipeline/edges/ (#4141, spec §6.2).

The node endpoints' rule applied to wiring: a request naming something the pipeline does not have is
refused, while a graph that is merely *wrong* -- a cycle, an End node nothing reaches -- persists and
is reported.

The body names its wires under ``wires``, a list whatever its length, so most of what follows
sends a list of one. ``post_wires`` writes that wrapper, leaving each test to name wires alone.
"""

import pytest

from apps.api.v2.pipeline_edit.serializers import MAX_WIRES_PER_CALL
from apps.pipelines.models import Node
from apps.utils.factories.experiment import ChatbotFactory

from .conftest import (
    add_bare_node,
    add_llm_node,
    add_router_node,
    edge_url,
    edges_url,
    inspect_url,
    node_url,
    nodes_url,
    post_wires,
    stored_edges,
    wire,
    wire_all,
    wire_refusals,
)

#: A wire naming two nodes no pipeline has, so a body of any length is refused whatever its length
#: check does. Repeated to build an oversized body without needing that many real nodes.
NOWHERE_WIRE = {"source": "CodeNode-nope1", "target": "CodeNode-nope2"}


@pytest.fixture()
def llm_node(client, chatbot, llm) -> str:
    return add_llm_node(client, chatbot, llm)


@pytest.fixture()
def router(client, chatbot, llm) -> str:
    """A two-branch router, so a test has a source offering more than one output handle."""
    return add_router_node(client, chatbot, llm)


def edges_from(pipeline, source: str) -> list[dict]:
    return [edge for edge in stored_edges(pipeline) if edge["source"] == source]


@pytest.mark.django_db()
class TestWhatAWireStores:
    def test_a_wire_stores_an_edge_with_a_server_assigned_id(self, client, chatbot, llm_node, end_node):
        """Edge ids are the server's to assign, built with the formula the UI builder's own ``addEdge``
        uses. Pinned in full because the trailing ``input`` is the one place the two diverge: the
        builder renders no target handle ids, so its own edges stop at the target's node id."""
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, "source_handle": "output"}])

        assert response.status_code == 201, response.content
        assert response.json()["edges"] == [
            {
                "id": f"reactflow__edge-{llm_node}output-{end_node}input",
                "source": llm_node,
                "target": end_node,
                "source_handle": "output",
                "target_handle": "input",
            }
        ]
        assert [edge["id"] for edge in edges_from(chatbot.pipeline, llm_node)] == [
            f"reactflow__edge-{llm_node}output-{end_node}input"
        ]

    @pytest.mark.parametrize("key", ["id", "edge_id"])
    def test_a_client_supplied_edge_id_is_refused(self, client, chatbot, llm_node, end_node, key):
        """Honouring a client's id would let one edge overwrite another: the patch engine merges
        edges by id. Both spellings, because the response calls it ``id`` while the path that deletes
        it is ``edge_id``, so each has to answer with the rule rather than "no such field".
        """
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, key: "mine-1"}])

        assert response.status_code == 400, response.content
        assert "server" in str(wire_refusals(response)[0][key]).lower()
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_colliding_edge_id_is_made_unique(self, client, chatbot, router, end_node):
        """The id form is derived from the endpoints, so a rewire can leave an old edge holding the
        id the next wire would draw -- and the patch engine treats an add whose id already exists as
        a no-op, which would answer 201 having stored nothing. Renaming the router's first keyword is
        what strands it: SCHEDULE moves to ``output_1`` and its edge follows, keeping its old id.
        """
        first = wire(client, chatbot, router, end_node, source_handle="output_0")
        client.patch(node_url(chatbot, router), {"params": {"keywords": ["book", "schedule"]}}, format="json")

        second = wire(client, chatbot, router, end_node, source_handle="output_0")

        assert second != first
        assert {edge["id"]: edge["sourceHandle"] for edge in edges_from(chatbot.pipeline, router)} == {
            first: "output_1",
            second: "output_0",
        }

    def test_a_wire_does_not_move_the_nodes_it_connects(self, client, chatbot, llm_node, end_node):
        """Phase 1 leaves the canvas alone (W11 is Phase 2): a node keeps the position it was parked
        at, so wiring cannot shuffle a layout someone arranged in the UI builder."""
        before = {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()}

        wire(client, chatbot, llm_node, end_node)

        chatbot.pipeline.refresh_from_db()
        after = {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()}
        assert after == before

    def test_a_wire_bumps_the_edit_revision(self, client, chatbot, llm_node, end_node):
        """The UI builder refuses a save whose ``base_revision`` has moved on, so an open builder
        session has to see this write as a conflict rather than overwrite it."""
        chatbot.pipeline.refresh_from_db()
        before = chatbot.pipeline.edit_revision

        wire(client, chatbot, llm_node, end_node)

        chatbot.pipeline.refresh_from_db()
        assert chatbot.pipeline.edit_revision == before + 1


@pytest.mark.django_db()
class TestHandleDefaults:
    """Which handle a wire lands on when the body does not say."""

    def test_a_router_source_must_name_the_branch_to_wire(self, client, chatbot, router, end_node):
        """A router's handles are its branches, so guessing one would wire a branch nobody chose. The
        refusal names the handles on offer, so the next call can pick one without a re-read."""
        response = post_wires(client, chatbot, [{"source": router, "target": end_node}])

        assert response.status_code == 400, response.content
        assert "output_0" in str(wire_refusals(response)[0]["source_handle"])
        assert "output_1" in str(wire_refusals(response)[0]["source_handle"])

    def test_a_multi_output_node_of_an_unpublished_type_must_still_name_its_branch(self, client, chatbot, end_node):
        """``BooleanNode`` offers two handles but is not a type the API publishes, so it can only reach
        a graph through the pipeline builder. Its branches still have to be named -- "which handle"
        is a question about the node in the graph, not about what POST /pipeline/nodes/ will create.
        """
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="BooleanNode-1", type="BooleanNode", params={})

        response = post_wires(client, chatbot, [{"source": node.flow_id, "target": end_node}])

        assert response.status_code == 400, response.content
        assert "output_0, output_1" in str(wire_refusals(response)[0]["source_handle"])

    def test_a_named_router_branch_is_wired_to_that_handle(self, client, chatbot, router, end_node):
        response = post_wires(client, chatbot, [{"source": router, "target": end_node, "source_handle": "output_1"}])

        assert response.status_code == 201, response.content
        assert response.json()["edges"][0]["source_handle"] == "output_1"

    @pytest.mark.parametrize(
        "handles",
        [
            pytest.param({}, id="omitted"),
            pytest.param({"source_handle": None, "target_handle": None}, id="null"),
            pytest.param({"source_handle": "output", "target_handle": "input"}, id="named"),
        ],
    )
    def test_the_handles_default_to_the_only_ones_the_nodes_offer(self, client, chatbot, llm_node, end_node, handles):
        """Every node type has exactly one, implicit, input handle, so naming it is never required.
        Null counts as omitted because ``GET /inspect/`` reports the UI builder's edges with a null
        ``target_handle``: a body built from one has to mean what a body without the key means.
        """
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, **handles}])

        assert response.status_code == 201, response.content
        assert response.json()["edges"][0]["source_handle"] == "output"
        assert response.json()["edges"][0]["target_handle"] == "input"
        stored = edges_from(chatbot.pipeline, llm_node)
        assert (stored[0]["sourceHandle"], stored[0]["targetHandle"]) == ("output", "input")


@pytest.mark.django_db()
class TestRefusedWires:
    """What the endpoint will not wire. All 400 rather than 404: an endpoint and a handle are *fields*
    of the body, so naming one the pipeline does not have is a bad body, not a bad address.
    """

    def test_a_source_that_is_not_a_node_in_this_pipeline_is_refused(self, client, chatbot, end_node):
        response = post_wires(client, chatbot, [{"source": "CodeNode-nope1", "target": end_node}])

        assert response.status_code == 400, response.content
        assert "CodeNode-nope1" in str(wire_refusals(response)[0]["source"])
        assert edges_from(chatbot.pipeline, "CodeNode-nope1") == []

    def test_a_target_that_is_not_a_node_in_this_pipeline_is_refused(self, client, chatbot, llm_node):
        response = post_wires(client, chatbot, [{"source": llm_node, "target": "CodeNode-nope1"}])

        assert response.status_code == 400, response.content
        assert "CodeNode-nope1" in str(wire_refusals(response)[0]["target"])

    def test_two_unknown_endpoints_are_both_reported(self, client, chatbot):
        """A client working from a stale read has both ends wrong as easily as one, so it learns both
        in a single call rather than one refusal at a time."""
        response = post_wires(client, chatbot, [{"source": "CodeNode-a", "target": "CodeNode-b"}])

        assert response.status_code == 400, response.content
        assert set(wire_refusals(response)[0]) == {"source", "target"}

    def test_a_node_from_another_chatbots_pipeline_is_refused(self, client, chatbot, llm, end_node):
        """Node ids are unique per pipeline, not globally, so "is a node" has to mean "is a node
        *here*" -- otherwise an edge lands whose source the graph cannot resolve."""
        elsewhere = ChatbotFactory.create(team=chatbot.team, name="Other bot", description="")

        response = post_wires(client, chatbot, [{"source": add_llm_node(client, elsewhere, llm), "target": end_node}])

        assert response.status_code == 400, response.content

    def test_a_source_handle_the_source_does_not_offer_is_refused(self, client, chatbot, llm_node, end_node):
        """An edge on a handle its source does not offer is dropped from the wired graph and reported
        stranded, so it is refused on the way in rather than stored to be complained about later."""
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, "source_handle": "output_7"}])

        assert response.status_code == 400, response.content
        assert "output" in str(wire_refusals(response)[0]["source_handle"])
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_routers_standard_output_handle_is_refused(self, client, chatbot, router, end_node):
        """A router offers ``output_0``/``output_1``, never the plain ``output`` every other node has
        -- the one wrong handle a client is most likely to send."""
        response = post_wires(client, chatbot, [{"source": router, "target": end_node, "source_handle": "output"}])

        assert response.status_code == 400, response.content
        assert edges_from(chatbot.pipeline, router) == []

    def test_a_wire_out_of_the_end_node_is_refused(self, client, chatbot, llm_node, end_node):
        """The End node offers no output handles: nothing runs after the end of the pipeline."""
        response = post_wires(client, chatbot, [{"source": end_node, "target": llm_node}])

        assert response.status_code == 400, response.content
        assert "no output" in str(wire_refusals(response)[0]["source"]).lower()

    def test_a_wire_into_the_start_node_is_refused(self, client, chatbot, llm_node, start_node):
        """The Start node has no input handle -- the UI builder draws none, so this is a connection a
        human could not make either."""
        response = post_wires(client, chatbot, [{"source": llm_node, "target": start_node}])

        assert response.status_code == 400, response.content
        assert "no input" in str(wire_refusals(response)[0]["target"]).lower()

    def test_a_target_handle_that_is_not_the_nodes_input_is_refused(self, client, chatbot, llm_node, end_node):
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, "target_handle": "output"}])

        assert response.status_code == 400, response.content
        assert "input" in str(wire_refusals(response)[0]["target_handle"])

    @pytest.mark.parametrize("missing", ["source", "target"])
    def test_a_body_missing_an_endpoint_is_refused(self, client, chatbot, llm_node, end_node, missing):
        body = {"source": llm_node, "target": end_node}
        del body[missing]

        response = post_wires(client, chatbot, [body])

        assert response.status_code == 400, response.content
        assert missing in wire_refusals(response)[0]

    def test_a_wire_out_of_a_node_of_an_unpublished_type_is_refused(self, client, chatbot, end_node):
        """A type naming no node class -- removed since, or never one -- has no handles the server
        can determine, so it cannot be wired *from*. Named as its own cause rather than sharing the
        End node's answer: nothing the caller does will make this node wirable.
        """
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="Gone-1", type="Gone", params={})

        response = post_wires(client, chatbot, [{"source": node.flow_id, "target": end_node}])

        assert response.status_code == 400, response.content
        assert "names no node type this server knows" in str(wire_refusals(response)[0]["source"])

    @pytest.mark.parametrize("node_type", ["RouterNode", "StaticRouterNode"])
    def test_a_router_with_no_keywords_yet_is_told_to_set_them(self, client, chatbot, end_node, node_type):
        """A router's handles *are* its keywords, and the minimal body `POST /pipeline/nodes/`
        advertises stores none -- so the one node type `source_handle` exists for arrives unwirable
        down the documented happy path. It must not share the End node's answer: "no edge can leave
        it" invites deleting the node, when the fix is one PATCH away.
        """
        node_id = add_bare_node(client, chatbot, node_type)

        response = post_wires(client, chatbot, [{"source": node_id, "target": end_node}])

        assert response.status_code == 400, response.content
        assert "keywords" in str(wire_refusals(response)[0]["source"])

    def test_a_router_can_be_wired_once_its_keywords_are_set(self, client, chatbot, end_node):
        """The other half of the test above: the refusal names a step that actually works."""
        node_id = add_bare_node(client, chatbot, "RouterNode")
        client.patch(node_url(chatbot, node_id), {"params": {"keywords": ["yes", "no"]}}, format="json")

        assert wire(client, chatbot, node_id, end_node, source_handle="output_0")

    def test_an_unrecognised_body_key_is_refused(self, client, chatbot, llm_node, end_node):
        """The body's shape is the API's own, so an unknown key is a typo worth reporting rather than
        something to drop silently."""
        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node, "colour": "red"}])

        assert response.status_code == 400, response.content
        assert "colour" in wire_refusals(response)[0]


@pytest.mark.django_db()
class TestDuplicateWires:
    """Re-wiring what is already wired is refused rather than stored twice, which is what makes the
    most retry-prone write in the API safe to retry (spec §8.2): a repeat leaves the graph exactly as
    the first call left it.
    """

    def test_wiring_the_same_pair_twice_is_refused(self, client, chatbot, llm_node, end_node):
        edge_id = wire(client, chatbot, llm_node, end_node)

        response = post_wires(client, chatbot, [{"source": llm_node, "target": end_node}])

        assert response.status_code == 400, response.content
        # The existing edge's id, so a client that never saw the first call's response can carry on
        # from the refusal rather than re-reading the whole pipeline to find it.
        assert edge_id in str(response.json())
        assert len(edges_from(chatbot.pipeline, llm_node)) == 1

    def test_a_body_wiring_the_same_pair_twice_is_refused(self, client, chatbot, llm_node, end_node):
        """The rule holds within one body as well as against the graph. There is no id to name yet,
        so the refusal names where the first of the two is instead."""
        response = post_wires(
            client, chatbot, [{"source": llm_node, "target": end_node}, {"source": llm_node, "target": end_node}]
        )

        assert response.status_code == 400, response.content
        assert set(wire_refusals(response)) == {1}
        assert "index 0" in str(wire_refusals(response)[1])
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_second_edge_from_the_same_handle_to_another_node_is_allowed(self, client, chatbot, llm, end_node):
        """Not a duplicate: one output handle may fan out, and the pipeline runs both branches."""
        source = add_llm_node(client, chatbot, llm)
        other = add_llm_node(client, chatbot, llm)

        first = wire(client, chatbot, source, end_node)
        second = wire(client, chatbot, source, other)

        assert first != second
        assert len(edges_from(chatbot.pipeline, source)) == 2

    def test_two_branches_of_a_router_may_share_a_target(self, client, chatbot, router, end_node):
        """Same source and target, different handle: two keywords routing to one node is ordinary."""
        first = wire(client, chatbot, router, end_node, source_handle="output_0")
        second = wire(client, chatbot, router, end_node, source_handle="output_1")

        assert first != second
        assert len(edges_from(chatbot.pipeline, router)) == 2


@pytest.mark.django_db()
class TestReportedRatherThanRefused:
    """A wire the server can act on lands even when it leaves the pipeline unbuildable: the agent is
    mid-build, and the errors report is how it learns what is still wrong.
    """

    def test_a_wire_that_closes_a_cycle_persists_and_is_reported(self, client, chatbot, llm):
        """A cycle is a property of the graph rather than of this one edge, so it lands in the
        graph-level bucket -- and the edge is stored, because which of the cycle's edges is the wrong
        one is the agent's call, not the server's."""
        first, second = (add_llm_node(client, chatbot, llm) for _ in range(2))
        wire(client, chatbot, first, second)

        response = post_wires(client, chatbot, [{"source": second, "target": first}])

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert body["pipeline_errors"]["pipeline"] == ["A cycle was detected"]
        assert body["edges"][0]["id"] in {edge["id"] for edge in stored_edges(chatbot.pipeline)}

    def test_a_wire_that_leaves_the_end_node_unreachable_persists_and_is_reported(self, client, chatbot, llm, end_node):
        """Wiring an island: the two nodes reach each other and nothing else, so the pipeline still
        cannot reach its End node."""
        chatbot.pipeline.data["edges"] = []
        chatbot.pipeline.save(update_fields=["data"])
        first, second = (add_llm_node(client, chatbot, llm) for _ in range(2))

        response = post_wires(client, chatbot, [{"source": first, "target": second}])

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert "not reachable" in body["pipeline_errors"]["node"][end_node]["root"]

    def test_a_wire_into_a_node_of_an_unpublished_type_lands(self, client, chatbot, llm_node):
        """The mirror of the refusal on the source side: a node whose type the API no longer publishes
        still accepts an edge, which is what lets a graph keep running while it is migrated off one."""
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="Gone-1", type="Gone", params={})

        response = post_wires(client, chatbot, [{"source": llm_node, "target": node.flow_id}])

        assert response.status_code == 201, response.content
        assert response.json()["edges"][0]["target_handle"] == "input"

    def test_a_self_loop_persists_and_is_reported(self, client, chatbot, llm_node):
        """The shortest cycle there is. Refusing it would be a special case for something the general
        cycle check already reports, and the node it names is the one the agent has to repair anyway."""
        response = post_wires(client, chatbot, [{"source": llm_node, "target": llm_node}])

        assert response.status_code == 201, response.content
        assert response.json()["pipeline_errors"]["pipeline"] == ["A cycle was detected"]

    def test_wiring_a_node_that_does_not_validate_still_lands(self, client, chatbot, end_node):
        """A node's own params are not this endpoint's business: an LLM node with no provider yet is
        exactly the half-built state an agent wires up before it fills in."""
        node_id = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json").json()["node"][
            "node_id"
        ]

        response = post_wires(client, chatbot, [{"source": node_id, "target": end_node}])

        assert response.status_code == 201, response.content
        assert "llm_provider_id" in response.json()["pipeline_errors"]["node"][node_id]


@pytest.mark.django_db()
class TestTheReadWriteLoop:
    """The id a wire returns is the id the read side reports, which is the id the delete takes."""

    def test_the_id_a_wire_returns_is_the_one_inspect_reports_and_delete_takes(
        self, client, chatbot, llm_node, end_node
    ):
        edge_id = wire(client, chatbot, llm_node, end_node)

        graph = client.get(inspect_url(chatbot)).json()["pipeline"]["graph"]

        assert {
            "id": edge_id,
            "source": llm_node,
            "target": end_node,
            "source_handle": "output",
            "target_handle": "input",
        } in graph["edges"]
        assert client.delete(edge_url(chatbot, edge_id)).status_code == 200


@pytest.mark.django_db()
class TestUnwiredHandles:
    """``unwired_handles`` is the advisory list an agent works down to finish a graph, so a wire has
    to clear it at both ends of the edge."""

    def test_a_wire_clears_the_handle_at_each_end(self, client, chatbot, llm, start_node):
        source = add_llm_node(client, chatbot, llm)
        target = add_llm_node(client, chatbot, llm)
        before = post_wires(client, chatbot, [{"source": start_node, "target": source}]).json()
        assert before["unwired_handles"][source] == [{"handle": "output", "label": None}]

        body = post_wires(client, chatbot, [{"source": source, "target": target}]).json()

        assert source not in body["unwired_handles"]
        assert body["unwired_handles"][target] == [{"handle": "output", "label": None}]

    def test_wiring_one_router_branch_leaves_the_others_listed(self, client, chatbot, router, end_node):
        body = post_wires(client, chatbot, [{"source": router, "target": end_node, "source_handle": "output_0"}]).json()

        assert body["unwired_handles"][router] == [
            {"handle": "input", "label": None},
            {"handle": "output_1", "label": "RESCHEDULE"},
        ]


@pytest.mark.django_db()
class TestWiringSeveralAtOnce:
    """One call carries as many wires as the client likes, and lands all of them or none -- so a
    whole branch can be laid out in one call without leaving half of it behind to unpick.
    """

    def test_every_wire_in_the_body_lands(self, client, chatbot, router, llm, end_node):
        other = add_llm_node(client, chatbot, llm)

        wired = wire_all(
            client,
            chatbot,
            [
                {"source": router, "target": other, "source_handle": "output_0"},
                {"source": router, "target": end_node, "source_handle": "output_1"},
                {"source": other, "target": end_node},
            ],
        )

        assert len(wired) == 3
        assert set(wired) <= {edge["id"] for edge in stored_edges(chatbot.pipeline)}

    def test_the_edges_come_back_in_the_bodys_order(self, client, chatbot, llm, end_node):
        """The response is how a client learns the ids it did not choose, so it has to be able to
        tell which id belongs to which wire it sent."""
        first, second = (add_llm_node(client, chatbot, llm) for _ in range(2))

        response = post_wires(
            client, chatbot, [{"source": second, "target": end_node}, {"source": first, "target": second}]
        )

        assert response.status_code == 201, response.content
        assert [(edge["source"], edge["target"]) for edge in response.json()["edges"]] == [
            (second, end_node),
            (first, second),
        ]

    def test_one_refused_wire_leaves_the_whole_body_unwired(self, client, chatbot, llm_node, end_node):
        """The good wire in front of the bad one is not stored either, so a client sending the
        corrected body again cannot trip over half of its own first attempt."""
        response = post_wires(
            client,
            chatbot,
            [{"source": llm_node, "target": end_node}, {"source": llm_node, "target": "CodeNode-nope1"}],
        )

        assert response.status_code == 400, response.content
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_refused_body_leaves_the_edit_revision_alone(self, client, chatbot, llm_node, end_node):
        """Nothing was written at all, rather than written and rolled back to the same values: an
        open pipeline builder session has no conflict to see."""
        chatbot.pipeline.refresh_from_db()
        before = chatbot.pipeline.edit_revision

        response = post_wires(client, chatbot, [{"source": llm_node, "target": "CodeNode-nope1"}])

        assert response.status_code == 400, response.content
        chatbot.pipeline.refresh_from_db()
        assert chatbot.pipeline.edit_revision == before

    def test_a_call_bumps_the_edit_revision_once_however_many_wires_it_carries(self, client, chatbot, llm, end_node):
        """One call is one save, so the builder sees one conflict rather than one per wire."""
        first, second = (add_llm_node(client, chatbot, llm) for _ in range(2))
        chatbot.pipeline.refresh_from_db()
        before = chatbot.pipeline.edit_revision

        wire_all(client, chatbot, [{"source": first, "target": second}, {"source": second, "target": end_node}])

        chatbot.pipeline.refresh_from_db()
        assert chatbot.pipeline.edit_revision == before + 1

    def test_every_refused_wire_is_reported_at_its_own_index(self, client, chatbot, llm_node, end_node):
        """Keyed by the position of the wire at fault, so a client working from a stale read of the
        graph learns everything wrong with its call in one round trip. A wire that is fine has no
        entry, which is what makes the keys worth reading."""
        response = post_wires(
            client,
            chatbot,
            [
                {"source": "CodeNode-nope1", "target": end_node},
                {"source": llm_node, "target": end_node},
                {"source": llm_node, "target": end_node, "source_handle": "output_7"},
            ],
        )

        assert response.status_code == 400, response.content
        assert {index: set(refusal) for index, refusal in wire_refusals(response).items()} == {
            0: {"source"},
            2: {"source_handle"},
        }

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param([{"source": "CodeNode-a1b2c", "target": "EndNode-b2c3d"}], id="bare-list-of-wires"),
            pytest.param({"source": "CodeNode-a1b2c", "target": "EndNode-b2c3d"}, id="bare-single-wire"),
        ],
    )
    def test_a_body_that_skips_the_wires_key_is_refused_with_the_shape_to_send(self, client, chatbot, body):
        """The two bodies a client is most likely to try first -- the wires with nothing around them,
        and a lone wire -- so each refusal says what to send instead rather than only what is wrong.
        """
        response = client.post(edges_url(chatbot), body, format="json")

        assert response.status_code == 400, response.content
        assert "wires" in str(response.json())

    def test_an_empty_body_is_refused(self, client, chatbot):
        """A call that would wire nothing is a mistake worth reporting, not a 201 that wrote nothing."""
        response = post_wires(client, chatbot, [])

        assert response.status_code == 400, response.content
        assert response.json()["wires"] == ["Name at least one wire to add."]

    def test_a_body_over_the_wire_limit_is_refused_before_any_wire_is_read(self, client, chatbot):
        """Each wire is checked against every wire before it, under the pipeline's row lock, so an
        unbounded body would hold that lock against every other writer for the square of its length.

        Refused on length alone: one message about `wires` itself rather than the per-wire refusals
        the next test gets, which is what says the wires were never looked at.
        """
        response = post_wires(client, chatbot, [NOWHERE_WIRE] * (MAX_WIRES_PER_CALL + 1))

        assert response.status_code == 400, response.content
        assert response.json() == {
            "wires": [f"Name at most {MAX_WIRES_PER_CALL} wires per call; split a larger graph across calls."]
        }

    def test_a_body_at_the_wire_limit_clears_the_length_check(self, client, chatbot):
        """The limit is a body's largest accepted length, not its largest refused one -- so a body of
        exactly that many is read as wires, and refused here only for the nodes it names.
        """
        response = post_wires(client, chatbot, [NOWHERE_WIRE] * MAX_WIRES_PER_CALL)

        assert response.status_code == 400, response.content
        assert len(wire_refusals(response)) == MAX_WIRES_PER_CALL
