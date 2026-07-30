import pytest

from apps.pipelines.flow import Flow, FlowNode, node_position_fields, react_flow_node_type, split_flow_data


def _full_flow():
    """A full graph: every node carries its content under ``data``."""
    return Flow(
        **{
            "nodes": [
                {
                    "id": "start-1",
                    "type": "startNode",
                    "position": {"x": 100, "y": 200},
                    "data": {"id": "start-1", "type": "StartNode", "label": "", "params": {"name": "start"}},
                },
                {
                    "id": "llm-1",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "id": "llm-1",
                        "type": "LLMResponseWithPrompt",
                        "label": "LLM",
                        "params": {"name": "llm-1", "prompt": "Be helpful"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "start-1", "target": "llm-1"}],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )


class TestSplitFlowData:
    def test_the_edge_data_holds_no_nodes(self):
        edge_data, _ = split_flow_data(_full_flow())

        assert "nodes" not in edge_data.model_dump()

    def test_maps_content_carrying_nodes_to_themselves(self):
        flow = _full_flow()
        _, node_data = split_flow_data(flow)

        assert node_data == {"start-1": flow.nodes[0], "llm-1": flow.nodes[1]}
        assert node_data["start-1"].data.type == "StartNode"
        assert node_data["start-1"].position == {"x": 100, "y": 200}

    def test_preserves_edges_and_drops_unknown_top_level_keys(self):
        flow = _full_flow()
        edge_data, _ = split_flow_data(flow)

        assert edge_data.edges == flow.edges
        assert "viewport" not in edge_data.model_dump()

    def test_content_less_nodes_are_membership_only(self):
        """A node with no ``data`` maps to None: it stays part of the graph membership (so its
        row must already exist) but supplies no content."""
        flow = Flow(nodes=[{"id": "start-1", "type": "startNode", "position": {"x": 1, "y": 2}}], edges=[])

        edge_data, node_data = split_flow_data(flow)

        assert edge_data.edges == []
        assert node_data == {"start-1": None}

    def test_does_not_mutate_the_flow(self):
        flow = _full_flow()
        split_flow_data(flow)

        assert [node.data.type for node in flow.nodes] == ["StartNode", "LLMResponseWithPrompt"]

    def test_flow_without_nodes_yields_no_membership(self):
        edge_data, node_data = split_flow_data(Flow(edges=[]))

        assert edge_data.edges == []
        assert node_data == {}


class TestReactFlowNodeType:
    @pytest.mark.parametrize(
        ("node_type", "expected"),
        [
            pytest.param("StartNode", "startNode", id="start"),
            pytest.param("EndNode", "endNode", id="end"),
            pytest.param("LLMResponseWithPrompt", "pipelineNode", id="regular"),
            pytest.param("RenderTemplate", "pipelineNode", id="another-regular"),
        ],
    )
    def test_maps_node_type_to_react_flow_type(self, node_type, expected):
        assert react_flow_node_type(node_type) == expected


class TestNodePositionFields:
    def test_maps_position_to_column_values(self):
        assert node_position_fields({"x": 10.7, "y": -3.2}) == {"position_x": 10.7, "position_y": -3.2}

    @pytest.mark.parametrize(
        "position",
        [
            pytest.param(None, id="absent"),
            pytest.param("garbage", id="not-a-dict"),
            pytest.param({"x": "abc", "y": 2}, id="non-numeric"),
            pytest.param({"x": 1}, id="missing-axis"),
            pytest.param({}, id="empty"),
        ],
    )
    def test_unusable_position_yields_no_fields(self, position):
        assert node_position_fields(position) == {}


class TestFlowNodeParsing:
    def test_parses_layout_only_node(self):
        node = FlowNode(**{"id": "n1", "type": "pipelineNode", "position": {"x": 1, "y": 2}})

        assert node.data is None

    def test_parses_full_node(self):
        node = FlowNode(**{"id": "n1", "type": "pipelineNode", "data": {"id": "n1", "type": "StartNode", "params": {}}})

        assert node.data.type == "StartNode"
