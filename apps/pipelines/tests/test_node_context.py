from unittest.mock import MagicMock, patch

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext, PipelineAccessor, StateAccessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_state(**overrides) -> PipelineState:
    """Build a PipelineState with sensible defaults for testing NodeContext."""
    defaults = {
        "messages": ["hello"],
        "outputs": {},
        "experiment_session": MagicMock(),
        "last_node_input": "hello",
        "node_inputs": ["hello"],
        "temp_state": {},
    }
    defaults.update(overrides)
    return PipelineState(defaults)


# ===========================================================================
# NodeContext top-level properties
# ===========================================================================


class TestNodeContextInput:
    def test_returns_last_node_input(self):
        state = _minimal_state(last_node_input="some input")
        ctx = NodeContext(state)
        assert ctx.input == "some input"

    def test_returns_empty_string_when_set(self):
        state = _minimal_state(last_node_input="")
        ctx = NodeContext(state)
        assert ctx.input == ""


class TestNodeContextInputs:
    def test_returns_node_inputs_list(self):
        state = _minimal_state(node_inputs=["a", "b", "c"])
        ctx = NodeContext(state)
        assert ctx.inputs == ["a", "b", "c"]

    def test_returns_single_element_list(self):
        state = _minimal_state(node_inputs=["only"])
        ctx = NodeContext(state)
        assert ctx.inputs == ["only"]


class TestNodeContextAttachments:
    def test_returns_attachments_from_temp_state(self):
        attachments = [{"file_id": 1, "type": "image"}]
        state = _minimal_state(temp_state={"attachments": attachments})
        ctx = NodeContext(state)
        assert ctx.attachments == attachments

    def test_returns_empty_list_when_no_attachments_key(self):
        state = _minimal_state(temp_state={"user_input": "hi"})
        ctx = NodeContext(state)
        assert ctx.attachments == []

    def test_returns_empty_list_when_temp_state_missing(self):
        state = _minimal_state()
        del state["temp_state"]
        ctx = NodeContext(state)
        assert ctx.attachments == []


class TestNodeContextSession:
    def test_returns_experiment_session(self):
        mock_session = MagicMock()
        mock_session.id = 42
        state = _minimal_state(experiment_session=mock_session)
        ctx = NodeContext(state)
        assert ctx.session is mock_session
        assert ctx.session.id == 42


class TestNodeContextInputMessageId:
    def test_returns_input_message_id(self):
        state = _minimal_state(input_message_id=99)
        ctx = NodeContext(state)
        assert ctx.input_message_id == 99

    def test_returns_none_when_missing(self):
        state = _minimal_state()
        ctx = NodeContext(state)
        assert ctx.input_message_id is None


class TestNodeContextInputMessageUrl:
    def test_returns_input_message_url(self):
        state = _minimal_state(input_message_url="https://example.com/msg/1")
        ctx = NodeContext(state)
        assert ctx.input_message_url == "https://example.com/msg/1"

    def test_returns_none_when_missing(self):
        state = _minimal_state()
        ctx = NodeContext(state)
        assert ctx.input_message_url is None


# ===========================================================================
# StateAccessor (context.state.*)
# ===========================================================================


class TestStateAccessorTemp:
    def test_returns_temp_state(self):
        state = _minimal_state(temp_state={"user_input": "hi", "outputs": {}})
        accessor = StateAccessor(state)
        assert accessor.temp == {"user_input": "hi", "outputs": {}}

    def test_returns_empty_dict_when_missing(self):
        state = _minimal_state()
        del state["temp_state"]
        accessor = StateAccessor(state)
        assert accessor.temp == {}


class TestStateAccessorSessionState:
    def test_returns_session_state(self):
        state = _minimal_state(session_state={"step": 3, "lang": "en"})
        accessor = StateAccessor(state)
        assert accessor.session_state == {"step": 3, "lang": "en"}

    def test_returns_empty_dict_when_missing(self):
        state = _minimal_state()
        accessor = StateAccessor(state)
        assert accessor.session_state == {}


class TestStateAccessorParticipantData:
    def test_returns_participant_data(self):
        state = _minimal_state(participant_data={"name": "Alice"})
        accessor = StateAccessor(state)
        assert accessor.participant_data == {"name": "Alice"}

    def test_returns_empty_dict_when_missing(self):
        state = _minimal_state()
        accessor = StateAccessor(state)
        assert accessor.participant_data == {}


class TestStateAccessorMergedParticipantData:
    @patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy.from_state")
    def test_returns_merged_data(self, mock_from_state):
        proxy = MagicMock()
        proxy.get.return_value = {"name": "Alice", "age": 30}
        mock_from_state.return_value = proxy

        state = _minimal_state()
        accessor = StateAccessor(state)
        result = accessor.merged_participant_data

        mock_from_state.assert_called_once_with(state)
        proxy.get.assert_called_once()
        assert result == {"name": "Alice", "age": 30}

    @patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy.from_state")
    def test_returns_empty_dict_when_proxy_returns_none(self, mock_from_state):
        proxy = MagicMock()
        proxy.get.return_value = None
        mock_from_state.return_value = proxy

        state = _minimal_state()
        accessor = StateAccessor(state)
        result = accessor.merged_participant_data

        assert result == {}

    @patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy.from_state")
    def test_returns_empty_dict_when_proxy_returns_empty(self, mock_from_state):
        proxy = MagicMock()
        proxy.get.return_value = {}
        mock_from_state.return_value = proxy

        state = _minimal_state()
        accessor = StateAccessor(state)
        assert accessor.merged_participant_data == {}


class TestStateAccessorUserInput:
    def test_returns_user_input(self):
        state = _minimal_state(temp_state={"user_input": "What is AI?"})
        accessor = StateAccessor(state)
        assert accessor.user_input == "What is AI?"

    def test_returns_empty_string_when_user_input_missing(self):
        state = _minimal_state(temp_state={"outputs": {}})
        accessor = StateAccessor(state)
        assert accessor.user_input == ""

    def test_returns_empty_string_when_temp_state_missing(self):
        state = _minimal_state()
        del state["temp_state"]
        accessor = StateAccessor(state)
        assert accessor.user_input == ""


# ===========================================================================
# PipelineAccessor (context.pipeline.*)
# ===========================================================================


def _state_with_outputs() -> PipelineState:
    """Build a state with realistic outputs and path for pipeline accessor tests."""
    return PipelineState(
        {
            "messages": ["hello"],
            "last_node_input": "hello",
            "node_inputs": ["hello"],
            "experiment_session": MagicMock(),
            "temp_state": {},
            "outputs": {
                "node_a": {"node_id": "id-a", "message": "output of A"},
                "router_b": {
                    "node_id": "id-b",
                    "message": "output of B",
                    "output_handle": "output0",
                    "route": "yes",
                },
                "node_c": {"node_id": "id-c", "message": "output of C"},
            },
            "path": [
                (None, "id-a", ["id-b"]),
                ("id-a", "id-b", ["id-c"]),
                ("id-b", "id-c", []),
            ],
        }
    )


class TestPipelineAccessorGetNodeOutput:
    def test_returns_node_output(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.get_node_output("node_a") == "output of A"

    def test_returns_none_for_missing_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.get_node_output("nonexistent") is None


class TestPipelineAccessorHasNodeOutput:
    def test_returns_true_for_existing_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.has_node_output("node_a") is True

    def test_returns_false_for_missing_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.has_node_output("nonexistent") is False

    def test_returns_false_when_outputs_missing(self):
        state = _minimal_state()
        del state["outputs"]
        accessor = PipelineAccessor(state)
        assert accessor.has_node_output("node_a") is False


class TestPipelineAccessorGetSelectedRoute:
    def test_returns_route_for_router_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.get_selected_route("router_b") == "yes"

    def test_returns_none_for_non_router_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.get_selected_route("node_a") is None

    def test_returns_none_for_missing_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        assert accessor.get_selected_route("nonexistent") is None


class TestPipelineAccessorGetAllRoutes:
    def test_returns_all_route_decisions(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        routes = accessor.get_all_routes()
        assert routes == {"router_b": "yes"}

    def test_returns_empty_dict_when_no_routes(self):
        state = _minimal_state(
            outputs={
                "node_a": {"node_id": "id-a", "message": "output"},
            }
        )
        accessor = PipelineAccessor(state)
        assert accessor.get_all_routes() == {}

    def test_returns_empty_dict_when_no_outputs(self):
        state = _minimal_state()
        accessor = PipelineAccessor(state)
        assert accessor.get_all_routes() == {}


class TestPipelineAccessorGetNodePath:
    def test_returns_path_for_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        path = accessor.get_node_path("node_c")
        assert path == ["node_a", "router_b", "node_c"]

    def test_returns_single_node_path_for_start_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        path = accessor.get_node_path("node_a")
        assert path == ["node_a"]

    def test_returns_single_element_for_unknown_node(self):
        state = _state_with_outputs()
        accessor = PipelineAccessor(state)
        path = accessor.get_node_path("nonexistent")
        assert path == ["nonexistent"]


# ===========================================================================
# NodeContext sub-object wiring
# ===========================================================================


class TestNodeContextSubObjects:
    def test_state_accessor_is_state_accessor_instance(self):
        state = _minimal_state()
        ctx = NodeContext(state)
        assert isinstance(ctx.state, StateAccessor)

    def test_pipeline_accessor_is_pipeline_accessor_instance(self):
        state = _minimal_state()
        ctx = NodeContext(state)
        assert isinstance(ctx.pipeline, PipelineAccessor)

    def test_state_and_pipeline_share_same_state(self):
        state = _minimal_state()
        ctx = NodeContext(state)
        assert ctx.state._state is ctx.pipeline._state

    def test_context_accesses_state_through_sub_accessor(self):
        """Verify context.state.* paths work end-to-end."""
        state = _minimal_state(
            temp_state={"user_input": "hi", "attachments": ["att1"]},
            session_state={"step": 1},
            participant_data={"name": "Bob"},
        )
        ctx = NodeContext(state)
        assert ctx.state.user_input == "hi"
        assert ctx.state.temp == {"user_input": "hi", "attachments": ["att1"]}
        assert ctx.state.session_state == {"step": 1}
        assert ctx.state.participant_data == {"name": "Bob"}


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_completely_empty_optional_fields(self):
        """When all optional fields are absent, sensible defaults are returned."""
        state = PipelineState(
            {
                "messages": ["msg"],
                "outputs": {},
                "experiment_session": MagicMock(),
                "last_node_input": "msg",
                "node_inputs": ["msg"],
            }
        )
        ctx = NodeContext(state)
        # Optional top-level properties
        assert ctx.attachments == []
        assert ctx.input_message_id is None
        assert ctx.input_message_url is None
        # StateAccessor defaults
        assert ctx.state.temp == {}
        assert ctx.state.session_state == {}
        assert ctx.state.participant_data == {}
        assert ctx.state.user_input == ""

    def test_temp_state_present_but_empty(self):
        state = _minimal_state(temp_state={})
        ctx = NodeContext(state)
        assert ctx.attachments == []
        assert ctx.state.user_input == ""

    def test_pipeline_accessor_with_empty_outputs(self):
        state = _minimal_state(outputs={})
        ctx = NodeContext(state)
        assert ctx.pipeline.has_node_output("anything") is False
        assert ctx.pipeline.get_node_output("anything") is None
        assert ctx.pipeline.get_all_routes() == {}

    @patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy.from_state")
    def test_merged_participant_data_via_context(self, mock_from_state):
        """Verify the full path context.state.merged_participant_data works."""
        proxy = MagicMock()
        proxy.get.return_value = {"key": "value"}
        mock_from_state.return_value = proxy

        state = _minimal_state()
        ctx = NodeContext(state)
        assert ctx.state.merged_participant_data == {"key": "value"}


# ===========================================================================
# Multiple routes in outputs
# ===========================================================================


class TestMultipleRoutes:
    def test_get_all_routes_with_multiple_routers(self):
        state = PipelineState(
            {
                "messages": ["hello"],
                "last_node_input": "hello",
                "node_inputs": ["hello"],
                "experiment_session": MagicMock(),
                "temp_state": {},
                "outputs": {
                    "router1": {
                        "node_id": "r1",
                        "message": "out1",
                        "route": "path_a",
                    },
                    "plain_node": {
                        "node_id": "n1",
                        "message": "out2",
                    },
                    "router2": {
                        "node_id": "r2",
                        "message": "out3",
                        "route": "path_b",
                    },
                },
                "path": [],
            }
        )
        accessor = PipelineAccessor(state)
        routes = accessor.get_all_routes()
        assert routes == {"router1": "path_a", "router2": "path_b"}

    def test_has_node_output_for_router_node(self):
        state = PipelineState(
            {
                "messages": ["hello"],
                "last_node_input": "hello",
                "node_inputs": ["hello"],
                "experiment_session": MagicMock(),
                "temp_state": {},
                "outputs": {
                    "router1": {
                        "node_id": "r1",
                        "message": "out1",
                        "route": "path_a",
                    },
                },
                "path": [],
            }
        )
        accessor = PipelineAccessor(state)
        assert accessor.has_node_output("router1") is True
        assert accessor.get_node_output("router1") == "out1"


# ===========================================================================
# List outputs (node executed multiple times)
# ===========================================================================


class TestListOutputs:
    def test_get_node_output_returns_last_when_list(self):
        """When a node is executed multiple times, outputs is a list; get_node_output returns last."""
        state = PipelineState(
            {
                "messages": ["hello"],
                "last_node_input": "hello",
                "node_inputs": ["hello"],
                "experiment_session": MagicMock(),
                "temp_state": {},
                "outputs": {
                    "node_x": [
                        {"node_id": "id-x", "message": "first"},
                        {"node_id": "id-x", "message": "second"},
                    ],
                },
                "path": [],
            }
        )
        accessor = PipelineAccessor(state)
        assert accessor.get_node_output("node_x") == "second"

    def test_get_selected_route_returns_last_when_list(self):
        state = PipelineState(
            {
                "messages": ["hello"],
                "last_node_input": "hello",
                "node_inputs": ["hello"],
                "experiment_session": MagicMock(),
                "temp_state": {},
                "outputs": {
                    "router_y": [
                        {"node_id": "id-y", "message": "out1", "route": "first_route"},
                        {"node_id": "id-y", "message": "out2", "route": "second_route"},
                    ],
                },
                "path": [],
            }
        )
        accessor = PipelineAccessor(state)
        assert accessor.get_selected_route("router_y") == "second_route"
