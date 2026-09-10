"""Node updates that change output handles (#1452): a router's ``keywords``, or a type change
to/from a router. Edges already on one of those handles have to end up somewhere defensible, the
same way ``apps/api/v2/pipeline_edit`` already does it for a param-only edit --
``apply_pipeline_patch`` never did, for any update, until now.
"""

from apps.pipelines.flow import FlowEdge, FlowNode, FlowNodeData, NodeDiff, PipelineDiffPayload
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt, StaticRouterNode
from apps.pipelines.patching import apply_pipeline_patch


def make_flow_node(node_id: str, node_type: str = "Passthrough", params: dict | None = None) -> FlowNode:
    return FlowNode(
        id=node_id,
        type="pipelineNode",
        position={"x": 0, "y": 0},
        data=FlowNodeData(id=node_id, type=node_type, label=node_type, params=params or {"name": node_id}),
    )


def make_flow_edge(edge_id: str, source: str, target: str, source_handle: str | None = None) -> FlowEdge:
    kwargs = {"id": edge_id, "source": source, "target": target}
    if source_handle is not None:
        kwargs["sourceHandle"] = source_handle
    return FlowEdge(**kwargs)


class TestNodeUpdateRewiresHandles:
    def _graph(self, source_type: str, source_params: dict, edges: list[FlowEdge]) -> dict:
        return {
            "nodes": [
                make_flow_node("src", source_type, params=source_params).model_dump(),
                {"id": "t0", "type": "pipelineNode", "position": {"x": 0, "y": 0}, "data": None},
                {"id": "t1", "type": "pipelineNode", "position": {"x": 0, "y": 0}, "data": None},
            ],
            "edges": [edge.model_dump() for edge in edges],
        }

    def test_dropping_a_keyword_strands_and_removes_its_edge(self):
        """Param-only change, no type change: a router losing a keyword loses the branch, and the
        edge on that branch's handle has nowhere left to go."""
        graph = self._graph(
            StaticRouterNode.__name__,
            {"name": "router", "route_key": "k", "keywords": ["a", "b"]},
            edges=[
                make_flow_edge("e0", "src", "t0", source_handle="output_0"),
                make_flow_edge("e1", "src", "t1", source_handle="output_1"),
            ],
        )
        updated = make_flow_node(
            "src", StaticRouterNode.__name__, params={"name": "router", "route_key": "k", "keywords": ["a"]}
        )
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        assert {edge.id for edge in edge_data.edges} == {"e0"}

    def test_reordering_keywords_moves_the_edge_with_its_branch(self):
        """Handles are positional, so the edge has to follow its branch's label across the
        reorder, not stay on the same handle index."""
        graph = self._graph(
            StaticRouterNode.__name__,
            {"name": "router", "route_key": "k", "keywords": ["a", "b"]},
            edges=[
                make_flow_edge("e0", "src", "t0", source_handle="output_0"),
                make_flow_edge("e1", "src", "t1", source_handle="output_1"),
            ],
        )
        updated = make_flow_node(
            "src", StaticRouterNode.__name__, params={"name": "router", "route_key": "k", "keywords": ["b", "a"]}
        )
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        by_id = {edge.id: edge.sourceHandle for edge in edge_data.edges}
        assert by_id == {"e0": "output_1", "e1": "output_0"}

    def test_changing_type_from_router_to_plain_drops_edges_on_removed_handles(self):
        """A type change is just another update as far as the patch engine is concerned -- it
        should lose its old branches' edges exactly like a keyword edit does."""
        graph = self._graph(
            StaticRouterNode.__name__,
            {"name": "router", "route_key": "k", "keywords": ["a", "b"]},
            edges=[
                make_flow_edge("e0", "src", "t0", source_handle="output_0"),
                make_flow_edge("e1", "src", "t1", source_handle="output_1"),
            ],
        )
        updated = make_flow_node("src", LLMResponseWithPrompt.__name__, params={"name": "llm"})
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        assert edge_data.edges == []

    def test_changing_type_from_plain_to_router_drops_the_existing_edge(self):
        """The reverse direction: a single unlabeled output has no branch label to match against
        a router's named branches, so the old edge is stranded rather than guessed onto one."""
        graph = self._graph(
            LLMResponseWithPrompt.__name__,
            {"name": "llm"},
            edges=[make_flow_edge("e0", "src", "t0", source_handle="output")],
        )
        updated = make_flow_node(
            "src", StaticRouterNode.__name__, params={"name": "router", "route_key": "k", "keywords": ["a"]}
        )
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        assert edge_data.edges == []

    def test_editing_an_unrelated_param_leaves_edges_alone(self):
        """A plain node offers the same single output whatever is edited, so this is not this
        engine's business -- the common case for every non-router param edit."""
        graph = self._graph(
            LLMResponseWithPrompt.__name__,
            {"name": "llm"},
            edges=[make_flow_edge("e0", "src", "t0", source_handle="output")],
        )
        updated = make_flow_node("src", LLMResponseWithPrompt.__name__, params={"name": "llm", "prompt": "Be terse."})
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        assert [edge.sourceHandle for edge in edge_data.edges] == ["output"]

    def test_an_edge_already_stranded_before_the_edit_is_left_alone(self):
        """Only the handles this edit removed are followed -- an edge already on a handle the
        node never offered is not this edit's edge to clean up."""
        graph = self._graph(
            StaticRouterNode.__name__,
            {"name": "router", "route_key": "k", "keywords": ["a"]},
            edges=[make_flow_edge("stranded", "src", "t0", source_handle="output_7")],
        )
        updated = make_flow_node(
            "src", StaticRouterNode.__name__, params={"name": "router", "route_key": "k", "keywords": ["a", "b"]}
        )
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, _ = apply_pipeline_patch(graph, patch)

        assert {edge.id for edge in edge_data.edges} == {"stranded"}

    def test_updating_a_node_with_no_previous_content_does_not_crash(self):
        """A membership-only row (``data=None``) has no prior handles to diff against; the update
        just replaces it, same as before this feature existed."""
        graph = self._graph(LLMResponseWithPrompt.__name__, {"name": "llm"}, edges=[])
        graph["nodes"][0]["data"] = None
        updated = make_flow_node("src", LLMResponseWithPrompt.__name__, params={"name": "llm"})
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))

        edge_data, node_data = apply_pipeline_patch(graph, patch)

        assert node_data["src"].data.params["name"] == "llm"
        assert edge_data.edges == []
