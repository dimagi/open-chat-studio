from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession
    from apps.pipelines.nodes.base import PipelineState
    from apps.pipelines.repository import PipelineRepository


class StateAccessor:
    """Conventional read-only access to the pipeline state.

    Accessed via context.state -- groups temp, session, and participant data
    that nodes use for rendering, routing, and extraction.

    Note: "Read-only" is by convention, not enforcement. Properties return
    mutable dict references, so callers *could* mutate the underlying state.
    Nodes should treat these as read-only and use dedicated setters (e.g.
    CodeNode's set_temp_state_key) for mutations.
    """

    def __init__(self, state: PipelineState):
        self._state = state

    @property
    def temp(self) -> dict:
        """Transient per-execution state. Not persisted across invocations."""
        return self._state.get("temp_state", {})

    @property
    def session_state(self) -> dict:
        """Persisted per-session state (saved to ExperimentSession.state after execution).

        Note: This is the in-flight pipeline copy. For the DB-persisted value,
        use context.session.state directly.
        """
        return self._state.get("session_state", {})

    @property
    def participant_data(self) -> dict:
        return self._state.get("participant_data", {})

    @property
    def original_user_message(self) -> str:
        """The original user message that started this pipeline invocation.

        Distinct from context.input, which is the output of the previous node.
        """
        return self._state.get("temp_state", {}).get("user_input", "")


class PipelineAccessor:
    """Read-only introspection into pipeline execution.

    Accessed via context.pipeline -- provides access to outputs and routing
    decisions from previously executed nodes. Currently only used by CodeNode.
    """

    def __init__(self, state: PipelineState):
        self._state = state

    def _get_node_outputs_by_name(self, node_name: str) -> list[dict] | None:
        """Get the outputs of a node by its name."""
        outputs = self._state.get("outputs", {}).get(node_name)
        if outputs is not None:
            return outputs if isinstance(outputs, list) else [outputs]
        return None

    def _get_node_id(self, node_name: str) -> str | None:
        """Get a node ID from a node name."""
        outputs = self._get_node_outputs_by_name(node_name)
        return outputs[-1]["node_id"] if outputs else None

    def _get_node_name(self, node_id: str) -> str | None:
        """Get a node name from a node ID."""
        for name, output in self._state.get("outputs", {}).items():
            if isinstance(output, list):
                output = output[0] if output else None
            if output and output.get("node_id") == node_id:
                return name
        return None

    def get_node_output(self, node_name: str) -> Any:
        """Get the output of a previously executed node by name."""
        outputs = self._get_node_outputs_by_name(node_name)
        return outputs[-1]["message"] if outputs else None

    def has_node_output(self, node_name: str) -> bool:
        """Check whether a node has been executed (has an entry in outputs)."""
        return node_name in self._state.get("outputs", {})

    def get_selected_route(self, node_name: str) -> str | None:
        """Get the routing decision made by a router node."""
        outputs = self._get_node_outputs_by_name(node_name)
        return outputs[-1].get("route") if outputs else None

    def get_all_routes(self) -> dict:
        """Get all routing decisions made so far in this execution.

        Note that in parallel workflows only the most recent route for a particular node will be returned.
        """
        routes_dict = {}
        outputs = self._state.get("outputs", {})
        for node_name, node_data in outputs.items():
            if isinstance(node_data, list):
                node_data = node_data[-1]
            if "route" in node_data:
                routes_dict[node_name] = node_data["route"]
        return routes_dict

    def get_node_path(self, node_name: str) -> list | None:
        """Get the execution path leading to a node."""
        path = []
        current_name = node_name
        while current_name:
            path.insert(0, current_name)
            current_node_id = self._get_node_id(current_name)
            if not current_node_id:
                break

            for _, current, targets in self._state.get("path", []):
                if current_node_id in targets:
                    current_name = self._get_node_name(current)
                    break
            else:
                break

        return path


class NodeContext:
    """Access-controlled view of pipeline state for nodes."""

    def __init__(self, state: PipelineState, repo: PipelineRepository | None = None):
        self._pipeline_state = state
        self.state = StateAccessor(state)
        self.pipeline = PipelineAccessor(state)
        self.repo = repo

    @property
    def input(self) -> str:
        """The primary input for this node (from the previous node's output)."""
        return self._pipeline_state["last_node_input"]

    @property
    def inputs(self) -> list[str]:
        """All inputs available to this node (from multiple incoming edges)."""
        return self._pipeline_state["node_inputs"]

    @property
    def attachments(self) -> list:
        """File attachments from the user message."""
        return self._pipeline_state.get("temp_state", {}).get("attachments", [])

    @property
    def session(self) -> ExperimentSession | None:
        """The experiment session. Use for session.id, session.team, etc."""
        return self._pipeline_state.get("experiment_session")

    @property
    def input_message_id(self) -> int | None:
        return self._pipeline_state.get("input_message_id")

    @property
    def input_message_url(self) -> str | None:
        return self._pipeline_state.get("input_message_url")
