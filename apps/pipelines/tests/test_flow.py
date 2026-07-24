import pytest

from apps.pipelines.flow import FlowNode, node_position_fields, split_flow_data


def _full_flow_data():
    """A graph in the old on-the-wire format: node content embedded under each node's "data" key."""
    return {
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


class TestSplitFlowData:
    def test_strips_node_content_from_layout(self):
        layout, node_data = split_flow_data(_full_flow_data())

        assert layout["nodes"] == [
            {"id": "start-1", "type": "startNode", "position": {"x": 100, "y": 200}},
            {"id": "llm-1", "type": "pipelineNode", "position": {"x": 300, "y": 0}},
        ]

    def test_extracts_node_content_and_position_by_flow_id(self):
        _, node_data = split_flow_data(_full_flow_data())

        assert node_data == {
            "start-1": {
                "type": "StartNode",
                "label": "",
                "params": {"name": "start"},
                "position": {"x": 100, "y": 200},
            },
            "llm-1": {
                "type": "LLMResponseWithPrompt",
                "label": "LLM",
                "params": {"name": "llm-1", "prompt": "Be helpful"},
                "position": {"x": 300, "y": 0},
            },
        }

    def test_preserves_edges_and_unknown_top_level_keys(self):
        data = _full_flow_data()
        layout, _ = split_flow_data(data)

        assert layout["edges"] == data["edges"]
        assert layout["viewport"] == data["viewport"]

    def test_layout_only_input_passes_through(self):
        data = {
            "nodes": [{"id": "start-1", "type": "startNode", "position": {"x": 1, "y": 2}}],
            "edges": [],
        }
        layout, node_data = split_flow_data(data)

        assert layout == data
        assert node_data == {}

    def test_does_not_mutate_input(self):
        data = _full_flow_data()
        split_flow_data(data)

        assert "data" in data["nodes"][0]

    @pytest.mark.parametrize(
        "node",
        [
            pytest.param({"id": "n1"}, id="only-id"),
            pytest.param({"id": "n1", "type": "pipelineNode"}, id="no-position"),
        ],
    )
    def test_tolerates_missing_layout_keys(self, node):
        layout, node_data = split_flow_data({"nodes": [node], "edges": []})

        assert layout["nodes"] == [node]
        assert node_data == {}

    def test_missing_label_and_params_get_defaults(self):
        data = {
            "nodes": [{"id": "n1", "data": {"id": "n1", "type": "StartNode"}}],
            "edges": [],
        }
        _, node_data = split_flow_data(data)

        assert node_data == {"n1": {"type": "StartNode", "label": "", "params": {}, "position": None}}

    def test_data_without_nodes_key_passes_through(self):
        layout, node_data = split_flow_data({"edges": []})

        assert layout == {"edges": []}
        assert node_data == {}


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
