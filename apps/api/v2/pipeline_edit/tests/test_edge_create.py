"""POST /api/v2/chatbots/{id}/pipeline/edges/ (#4141, spec §6.2).

The same rule the node endpoints encode, applied to wiring: a request naming something the pipeline
does not have is refused, while a graph that is merely *wrong* -- a cycle, an End node nothing
reaches -- persists and is reported. What separates the two is whether the server could act on the
request at all.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.experiment import ChatbotFactory

from .conftest import (
    add_bare_node,
    add_edge,
    add_llm_node,
    add_router_node,
    edges_url,
    node_url,
    nodes_url,
    stored_edges,
    wire,
)


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
    def test_a_wire_stores_an_edge_with_a_server_assigned_id(self, client, chatbot, llm_node, end):
        """Edge ids are the server's to assign, built with the formula the UI builder's own ``addEdge``
        uses. Pinned in full because the trailing ``input`` is the one place the two diverge: the
        builder renders no target handle ids, so its own edges stop at the target's node id."""
        response = client.post(
            edges_url(chatbot), {"source": llm_node, "target": end, "source_handle": "output"}, format="json"
        )

        assert response.status_code == 201, response.content
        assert response.json()["edge"] == {
            "id": f"reactflow__edge-{llm_node}output-{end}input",
            "source": llm_node,
            "target": end,
            "source_handle": "output",
            "target_handle": "input",
        }
        assert [edge["id"] for edge in edges_from(chatbot.pipeline, llm_node)] == [
            f"reactflow__edge-{llm_node}output-{end}input"
        ]

    @pytest.mark.parametrize("key", ["id", "edge_id"])
    def test_a_client_supplied_edge_id_is_refused(self, client, chatbot, llm_node, end, key):
        """Honouring a client's id would let one edge overwrite another: the patch engine merges edges
        by id.

        Both spellings, because the response calls it ``id`` while the path that deletes it is
        ``edge_id`` -- a client may reasonably reach for either, and each has to answer with the rule
        rather than with "no such field".
        """
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": end, key: "mine-1"}, format="json")

        assert response.status_code == 400, response.content
        assert "server" in str(response.json()[key]).lower()
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_colliding_edge_id_is_made_unique(self, client, chatbot, router, end):
        """The id form is derived from the endpoints, so a rewire can leave an old edge holding the id
        the next wire would draw -- and the patch engine treats an add whose id already exists as a
        no-op, which would answer 201 having stored nothing.

        Renaming the router's first keyword is what strands the id: SCHEDULE moves to ``output_1``
        and its edge follows, while the edge keeps the id it was created with.
        """
        first = wire(client, chatbot, router, end, source_handle="output_0")
        client.patch(node_url(chatbot, router), {"params": {"keywords": ["book", "schedule"]}}, format="json")

        second = wire(client, chatbot, router, end, source_handle="output_0")

        assert second != first
        assert {edge["id"]: edge["sourceHandle"] for edge in edges_from(chatbot.pipeline, router)} == {
            first: "output_1",
            second: "output_0",
        }

    def test_a_wire_does_not_move_the_nodes_it_connects(self, client, chatbot, llm_node, end):
        """Phase 1 leaves the canvas alone (W11 is Phase 2): a node keeps the position it was parked
        at, so wiring cannot shuffle a layout someone arranged in the UI builder."""
        before = {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()}

        wire(client, chatbot, llm_node, end)

        chatbot.pipeline.refresh_from_db()
        after = {node.flow_id: (node.position_x, node.position_y) for node in chatbot.pipeline.node_set.all()}
        assert after == before

    def test_a_wire_bumps_the_edit_revision(self, client, chatbot, llm_node, end):
        """The UI builder refuses a save whose ``base_revision`` has moved on, so an open builder
        session has to see this write as a conflict rather than overwrite it."""
        chatbot.pipeline.refresh_from_db()
        before = chatbot.pipeline.edit_revision

        wire(client, chatbot, llm_node, end)

        chatbot.pipeline.refresh_from_db()
        assert chatbot.pipeline.edit_revision == before + 1


@pytest.mark.django_db()
class TestHandleDefaults:
    """Which handle a wire lands on when the body does not say."""

    def test_a_source_offering_one_output_handle_needs_no_source_handle(self, client, chatbot, llm_node, end):
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": end}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["edge"]["source_handle"] == "output"

    def test_a_router_source_must_name_the_branch_to_wire(self, client, chatbot, router, end):
        """A router's handles are its branches, so guessing one would wire a branch nobody chose. The
        refusal names the handles on offer, so the next call can pick one without a re-read."""
        response = client.post(edges_url(chatbot), {"source": router, "target": end}, format="json")

        assert response.status_code == 400, response.content
        assert "output_0" in str(response.json()["source_handle"])
        assert "output_1" in str(response.json()["source_handle"])

    def test_a_multi_output_node_of_an_unpublished_type_must_still_name_its_branch(self, client, chatbot, end):
        """``BooleanNode`` offers two handles but is not a type the API publishes, so it can only reach
        a graph through the pipeline builder. Its branches still have to be named -- "which handle"
        is a question about the node in the graph, not about what POST /pipeline/nodes/ will create.
        """
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="BooleanNode-1", type="BooleanNode", params={})

        response = client.post(edges_url(chatbot), {"source": node.flow_id, "target": end}, format="json")

        assert response.status_code == 400, response.content
        assert "output_0, output_1" in str(response.json()["source_handle"])

    def test_a_named_router_branch_is_wired_to_that_handle(self, client, chatbot, router, end):
        response = client.post(
            edges_url(chatbot), {"source": router, "target": end, "source_handle": "output_1"}, format="json"
        )

        assert response.status_code == 201, response.content
        assert response.json()["edge"]["source_handle"] == "output_1"

    @pytest.mark.parametrize(
        "handles",
        [
            pytest.param({}, id="omitted"),
            pytest.param({"source_handle": None, "target_handle": None}, id="null"),
            pytest.param({"source_handle": "output", "target_handle": "input"}, id="named"),
        ],
    )
    def test_the_handles_default_to_the_only_ones_the_nodes_offer(self, client, chatbot, llm_node, end, handles):
        """Every node type has exactly one, implicit, input handle, so naming it is never required.
        Null counts as omitted because ``GET /inspect/`` reports the UI builder's edges with a null
        ``target_handle``: a body built from one has to mean what a body without the key means.
        """
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": end, **handles}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["edge"]["source_handle"] == "output"
        assert response.json()["edge"]["target_handle"] == "input"
        stored = edges_from(chatbot.pipeline, llm_node)
        assert (stored[0]["sourceHandle"], stored[0]["targetHandle"]) == ("output", "input")


@pytest.mark.django_db()
class TestRefusedWires:
    """What the endpoint will not wire. All 400 rather than 404: an endpoint and a handle are *fields*
    of the body, so naming one the pipeline does not have is a bad body, not a bad address.
    """

    def test_a_source_that_is_not_a_node_in_this_pipeline_is_refused(self, client, chatbot, end):
        response = client.post(edges_url(chatbot), {"source": "CodeNode-nope1", "target": end}, format="json")

        assert response.status_code == 400, response.content
        assert "CodeNode-nope1" in str(response.json()["source"])
        assert edges_from(chatbot.pipeline, "CodeNode-nope1") == []

    def test_a_target_that_is_not_a_node_in_this_pipeline_is_refused(self, client, chatbot, llm_node):
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": "CodeNode-nope1"}, format="json")

        assert response.status_code == 400, response.content
        assert "CodeNode-nope1" in str(response.json()["target"])

    def test_two_unknown_endpoints_are_both_reported(self, client, chatbot):
        """A client working from a stale read has both ends wrong as easily as one, so it learns both
        in a single call rather than one refusal at a time."""
        response = client.post(edges_url(chatbot), {"source": "CodeNode-a", "target": "CodeNode-b"}, format="json")

        assert response.status_code == 400, response.content
        assert set(response.json()) == {"source", "target"}

    def test_a_node_from_another_chatbots_pipeline_is_refused(self, client, chatbot, llm, end):
        """Node ids are unique per pipeline, not globally, so "is a node" has to mean "is a node
        *here*" -- otherwise an edge lands whose source the graph cannot resolve."""
        elsewhere = ChatbotFactory.create(team=chatbot.team, name="Other bot", description="")

        response = client.post(
            edges_url(chatbot), {"source": add_llm_node(client, elsewhere, llm), "target": end}, format="json"
        )

        assert response.status_code == 400, response.content

    def test_a_source_handle_the_source_does_not_offer_is_refused(self, client, chatbot, llm_node, end):
        """An edge on a handle its source does not offer is dropped from the wired graph and reported
        stranded, so it is refused on the way in rather than stored to be complained about later."""
        response = client.post(
            edges_url(chatbot), {"source": llm_node, "target": end, "source_handle": "output_7"}, format="json"
        )

        assert response.status_code == 400, response.content
        assert "output" in str(response.json()["source_handle"])
        assert edges_from(chatbot.pipeline, llm_node) == []

    def test_a_routers_standard_output_handle_is_refused(self, client, chatbot, router, end):
        """A router offers ``output_0``/``output_1``, never the plain ``output`` every other node has
        -- the one wrong handle a client is most likely to send."""
        response = client.post(
            edges_url(chatbot), {"source": router, "target": end, "source_handle": "output"}, format="json"
        )

        assert response.status_code == 400, response.content
        assert edges_from(chatbot.pipeline, router) == []

    def test_a_wire_out_of_the_end_node_is_refused(self, client, chatbot, llm_node, end):
        """The End node offers no output handles: nothing runs after the end of the pipeline."""
        response = client.post(edges_url(chatbot), {"source": end, "target": llm_node}, format="json")

        assert response.status_code == 400, response.content
        assert "no output" in str(response.json()["source"]).lower()

    def test_a_wire_into_the_start_node_is_refused(self, client, chatbot, llm_node, start):
        """The Start node has no input handle -- the UI builder draws none, so this is a connection a
        human could not make either."""
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": start}, format="json")

        assert response.status_code == 400, response.content
        assert "no input" in str(response.json()["target"]).lower()

    def test_a_target_handle_that_is_not_the_nodes_input_is_refused(self, client, chatbot, llm_node, end):
        response = client.post(
            edges_url(chatbot), {"source": llm_node, "target": end, "target_handle": "output"}, format="json"
        )

        assert response.status_code == 400, response.content
        assert "input" in str(response.json()["target_handle"])

    @pytest.mark.parametrize("missing", ["source", "target"])
    def test_a_body_missing_an_endpoint_is_refused(self, client, chatbot, llm_node, end, missing):
        body = {"source": llm_node, "target": end}
        del body[missing]

        response = client.post(edges_url(chatbot), body, format="json")

        assert response.status_code == 400, response.content
        assert missing in response.json()

    def test_a_wire_out_of_a_node_of_an_unpublished_type_is_refused(self, client, chatbot, end):
        """A type naming no node class -- removed since, or never one -- has no handles the server can
        determine, so it cannot be wired *from*: the edge would be reported stranded.

        Named as its own cause rather than sharing the End node's answer: nothing the caller does will
        make this node wirable, so "unwire and move off this type" is the only way forward.
        """
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="Gone-1", type="Gone", params={})

        response = client.post(edges_url(chatbot), {"source": node.flow_id, "target": end}, format="json")

        assert response.status_code == 400, response.content
        assert "does not publish" in str(response.json()["source"])

    @pytest.mark.parametrize("node_type", ["RouterNode", "StaticRouterNode"])
    def test_a_router_with_no_keywords_yet_is_told_to_set_them(self, client, chatbot, end, node_type):
        """A router's handles *are* its keywords, and `POST /pipeline/nodes/ {"type": "RouterNode"}` --
        the minimal body that endpoint advertises -- stores none. So the one node type `source_handle`
        exists for arrives unwirable, down the documented happy path.

        It must not share the End node's answer. "No edge can leave it" reads as "this node can never
        be a source", whose natural recovery is to delete the node; the truth is one PATCH away, and
        the message has to say which one.
        """
        node_id = add_bare_node(client, chatbot, node_type)

        response = client.post(edges_url(chatbot), {"source": node_id, "target": end}, format="json")

        assert response.status_code == 400, response.content
        assert "keywords" in str(response.json()["source"])

    def test_a_router_can_be_wired_once_its_keywords_are_set(self, client, chatbot, end, llm):
        """The other half of the test above: the refusal names a step that actually works."""
        node_id = add_bare_node(client, chatbot, "RouterNode")
        client.patch(node_url(chatbot, node_id), {"params": {"keywords": ["yes", "no"]}}, format="json")

        assert wire(client, chatbot, node_id, end, source_handle="output_0")

    def test_an_unrecognised_body_key_is_refused(self, client, chatbot, llm_node, end):
        """The body's shape is the API's own, so an unknown key is a typo worth reporting rather than
        something to drop silently."""
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": end, "colour": "red"}, format="json")

        assert response.status_code == 400, response.content
        assert "colour" in response.json()


@pytest.mark.django_db()
class TestDuplicateWires:
    """Re-wiring what is already wired is refused rather than stored twice, which is what makes the
    most retry-prone write in the API safe to retry (spec §8.2): a repeat leaves the graph exactly as
    the first call left it.
    """

    def test_wiring_the_same_pair_twice_is_refused(self, client, chatbot, llm_node, end):
        edge_id = wire(client, chatbot, llm_node, end)

        response = client.post(edges_url(chatbot), {"source": llm_node, "target": end}, format="json")

        assert response.status_code == 400, response.content
        # The existing edge's id, so a client that never saw the first call's response can carry on
        # from the refusal rather than re-reading the whole pipeline to find it.
        assert edge_id in str(response.json())
        assert len(edges_from(chatbot.pipeline, llm_node)) == 1

    def test_a_duplicate_of_an_edge_the_ui_builder_wrote_is_refused(self, client, chatbot, llm_node, end):
        """Every edge the UI builder draws stores a null ``targetHandle`` -- it renders no id on its
        target handles -- so a duplicate check comparing raw values would read its edge as a different
        wire and store a second edge beside it. Null on both sides here, since a stored
        ``sourceHandle`` is optional too."""
        add_edge(chatbot.pipeline, llm_node, end, source_handle=None, target_handle=None)

        response = client.post(
            edges_url(chatbot), {"source": llm_node, "target": end, "source_handle": "output"}, format="json"
        )

        assert response.status_code == 400, response.content
        assert len(edges_from(chatbot.pipeline, llm_node)) == 1

    def test_a_second_edge_from_the_same_handle_to_another_node_is_allowed(self, client, chatbot, llm, end):
        """Not a duplicate: one output handle may fan out, and the pipeline runs both branches."""
        source = add_llm_node(client, chatbot, llm)
        other = add_llm_node(client, chatbot, llm)

        first = wire(client, chatbot, source, end)
        second = wire(client, chatbot, source, other)

        assert first != second
        assert len(edges_from(chatbot.pipeline, source)) == 2

    def test_two_branches_of_a_router_may_share_a_target(self, client, chatbot, router, end):
        """Same source and target, different handle: two keywords routing to one node is ordinary."""
        first = wire(client, chatbot, router, end, source_handle="output_0")
        second = wire(client, chatbot, router, end, source_handle="output_1")

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

        response = client.post(edges_url(chatbot), {"source": second, "target": first}, format="json")

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert body["pipeline_errors"]["pipeline"] == ["A cycle was detected"]
        assert body["edge"]["id"] in {edge["id"] for edge in stored_edges(chatbot.pipeline)}

    def test_a_wire_that_leaves_the_end_node_unreachable_persists_and_is_reported(self, client, chatbot, llm, end):
        """Wiring an island: the two nodes reach each other and nothing else, so the pipeline still
        cannot reach its End node."""
        chatbot.pipeline.data["edges"] = []
        chatbot.pipeline.save(update_fields=["data"])
        first, second = (add_llm_node(client, chatbot, llm) for _ in range(2))

        response = client.post(edges_url(chatbot), {"source": first, "target": second}, format="json")

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert "not reachable" in body["pipeline_errors"]["node"][end]["root"]

    def test_a_wire_into_a_node_of_an_unpublished_type_lands(self, client, chatbot, llm_node):
        """The mirror of the refusal on the source side: a node whose type the API no longer publishes
        still accepts an edge, which is what lets a graph keep running while it is migrated off one."""
        node = Node.objects.create(pipeline=chatbot.pipeline, flow_id="Gone-1", type="Gone", params={})

        response = client.post(edges_url(chatbot), {"source": llm_node, "target": node.flow_id}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["edge"]["target_handle"] == "input"

    def test_a_self_loop_persists_and_is_reported(self, client, chatbot, llm_node):
        """The shortest cycle there is. Refusing it would be a special case for something the general
        cycle check already reports, and the node it names is the one the agent has to repair anyway."""
        response = client.post(edges_url(chatbot), {"source": llm_node, "target": llm_node}, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["pipeline_errors"]["pipeline"] == ["A cycle was detected"]

    def test_wiring_a_node_that_does_not_validate_still_lands(self, client, chatbot, end):
        """A node's own params are not this endpoint's business: an LLM node with no provider yet is
        exactly the half-built state an agent wires up before it fills in."""
        node_id = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json").json()["node"][
            "node_id"
        ]

        response = client.post(edges_url(chatbot), {"source": node_id, "target": end}, format="json")

        assert response.status_code == 201, response.content
        assert "llm_provider_id" in response.json()["pipeline_errors"]["node"][node_id]


@pytest.mark.django_db()
class TestTheReadWriteLoop:
    """The id a wire returns is the id the read side reports, which is the id the delete takes."""

    def test_the_id_a_wire_returns_is_the_one_inspect_reports_and_delete_takes(self, client, chatbot, llm_node, end):
        edge_id = wire(client, chatbot, llm_node, end)

        graph = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()["pipeline"]["graph"]

        assert {
            "id": edge_id,
            "source": llm_node,
            "target": end,
            "source_handle": "output",
            "target_handle": "input",
        } in graph["edges"]
        assert client.delete(f"/api/v2/chatbots/{chatbot.public_id}/pipeline/edges/{edge_id}/").status_code == 200


@pytest.mark.django_db()
class TestUnwiredHandles:
    """``unwired_handles`` is the advisory list an agent works down to finish a graph, so a wire has
    to clear it at both ends of the edge."""

    def test_a_wire_clears_the_handle_at_each_end(self, client, chatbot, llm, start):
        source = add_llm_node(client, chatbot, llm)
        target = add_llm_node(client, chatbot, llm)
        before = client.post(edges_url(chatbot), {"source": start, "target": source}, format="json").json()
        assert before["unwired_handles"][source] == [{"handle": "output", "label": None}]

        body = client.post(edges_url(chatbot), {"source": source, "target": target}, format="json").json()

        assert source not in body["unwired_handles"]
        assert body["unwired_handles"][target] == [{"handle": "output", "label": None}]

    def test_wiring_one_router_branch_leaves_the_others_listed(self, client, chatbot, router, end):
        body = client.post(
            edges_url(chatbot), {"source": router, "target": end, "source_handle": "output_0"}, format="json"
        ).json()

        assert body["unwired_handles"][router] == [
            {"handle": "input", "label": None},
            {"handle": "output_1", "label": "RESCHEDULE"},
        ]
