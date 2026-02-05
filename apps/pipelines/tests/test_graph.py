from unittest.mock import MagicMock, patch

from apps.pipelines.graph import PipelineGraph


class TestPipelineGraphRetryPolicy:
    @patch("apps.pipelines.graph.get_retry_policy")
    def test_nodes_added_with_retry_policy(self, mock_get_retry_policy):
        """Verify that nodes are added to the graph with a retry policy."""
        from langgraph.types import RetryPolicy

        mock_policy = RetryPolicy()
        mock_get_retry_policy.return_value = mock_policy

        # Create a minimal pipeline structure
        mock_pipeline = MagicMock()
        mock_start_node = MagicMock()
        mock_start_node.flow_id = "start"
        mock_start_node.label = "Start"
        mock_start_node.type = "StartNode"
        mock_start_node.params = {}

        mock_end_node = MagicMock()
        mock_end_node.flow_id = "end"
        mock_end_node.label = "End"
        mock_end_node.type = "EndNode"
        mock_end_node.params = {}

        mock_pipeline.node_set.all.return_value = [mock_start_node, mock_end_node]
        mock_pipeline.data = {"edges": [{"id": "e1", "source": "start", "target": "end"}]}

        with patch.object(PipelineGraph, "_check_for_cycles", return_value=False):
            graph = PipelineGraph.build_from_pipeline(mock_pipeline)

            # Mock the state_graph to capture add_node calls
            with patch("apps.pipelines.graph.StateGraph") as MockStateGraph:
                mock_state_graph = MagicMock()
                MockStateGraph.return_value = mock_state_graph

                graph.build_runnable()

                # Verify add_node was called with retry_policy
                for call in mock_state_graph.add_node.call_args_list:
                    _, kwargs = call
                    assert "retry_policy" in kwargs
                    assert kwargs["retry_policy"] == mock_policy
