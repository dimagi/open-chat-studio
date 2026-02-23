from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession
    from apps.pipelines.nodes.base import PipelineState


class StateAccessor:
    """Conventional read-only access to user-facing pipeline state.

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
        """In-flight participant data accumulated during this pipeline execution.
        Does NOT include persisted global_data from the database.
        For the merged view, use merged_participant_data.
        """
        return self._state.get("participant_data", {})

    @property
    def merged_participant_data(self) -> dict:
        """Participant data merged with persisted global_data from the database.
        This is what template rendering and routing nodes should use.
        """
        from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy

        return ParticipantDataProxy.from_state(self._state).get() or {}

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

    def get_node_output(self, node_name: str) -> Any:
        """Get the output of a previously executed node by name."""
        return self._state.get_node_output_by_name(node_name)

    def has_node_output(self, node_name: str) -> bool:
        """Check whether a node has been executed (has an entry in outputs)."""
        return node_name in self._state.get("outputs", {})

    def get_selected_route(self, node_name: str) -> str | None:
        """Get the routing decision made by a router node."""
        return self._state.get_selected_route(node_name)

    def get_all_routes(self) -> dict:
        """Get all routing decisions made so far in this execution."""
        return self._state.get_all_routes()

    def get_node_path(self, node_name: str) -> list | None:
        """Get the execution path leading to a node."""
        return self._state.get_node_path(node_name)


class NodeContext:
    """Access-controlled view of pipeline state for nodes.

    Provides typed, read-only access to the data nodes need.
    Hides system internals (path, node_source, raw outputs dict).
    The underlying ``_state`` attribute uses a single-underscore convention
    to signal that direct access is discouraged but not prevented.

    Top-level properties: node I/O and session context (used by every node).
    Sub-objects:
        context.state    -- user-facing state (temp, session, participant data)
        context.pipeline -- execution introspection (node outputs, routes)
    """

    def __init__(self, state: PipelineState):
        self._state = state
        self.state = StateAccessor(state)
        self.pipeline = PipelineAccessor(state)

    # --- Node input ---
    @property
    def input(self) -> str:
        """The primary input for this node (from the previous node's output)."""
        return self._state["last_node_input"]

    @property
    def inputs(self) -> list[str]:
        """All inputs available to this node (from multiple incoming edges)."""
        return self._state["node_inputs"]

    @property
    def attachments(self) -> list:
        """File attachments from the user message."""
        return self._state.get("temp_state", {}).get("attachments", [])

    # --- Session context ---
    @property
    def session(self) -> ExperimentSession | None:
        """The experiment session. Use for session.id, session.team, etc."""
        return self._state.get("experiment_session")

    @property
    def input_message_id(self) -> int | None:
        return self._state.get("input_message_id")

    @property
    def input_message_url(self) -> str | None:
        return self._state.get("input_message_url")
