from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from apps.evaluations.const import GENERATION_PROMPT_VARIABLE_MAX_LENGTH
from apps.pipelines.nodes.base import PipelineState

if TYPE_CHECKING:
    from apps.service_providers.tracing.capture_tracer import CapturedSpan

TRUNCATION_SUFFIX = " [truncated]"


def _format_tags(tags: list[tuple[str, str]]) -> list[str]:
    return [f"{category}:{tag}" if category else tag for tag, category in tags]


def _node_output(entry: dict | list[dict]) -> Any:
    if isinstance(entry, list):
        return [item["message"] for item in entry]
    return entry["message"]


def _call(span: CapturedSpan, state: PipelineState) -> dict:
    node = (state.get_node_name(span.node_id) or span.node_id) if span.node_id else None
    return {"node": node, "name": span.name, "input": span.input, "output": span.output, "error": span.error}


def _cap(value: str) -> str:
    if len(value) <= GENERATION_PROMPT_VARIABLE_MAX_LENGTH:
        return value
    return value[:GENERATION_PROMPT_VARIABLE_MAX_LENGTH] + TRUNCATION_SUFFIX


@dataclass(frozen=True)
class GenerationRecord:
    """What a bot generation did, for evaluators to judge beside the reply text."""

    node_outputs: dict[str, Any] = field(default_factory=dict)
    path: list[tuple[str | None, str, list[str]]] = field(default_factory=list)
    message_tags: list[str] = field(default_factory=list)
    session_tags: list[str] = field(default_factory=list)
    participant_data_diff: list = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)

    @classmethod
    def from_run(
        cls, pipeline_state: dict | None, participant_data_diff: list, spans: list[CapturedSpan]
    ) -> GenerationRecord:
        if pipeline_state is None:
            return cls()
        state = PipelineState(pipeline_state)
        return cls(
            node_outputs={name: _node_output(entry) for name, entry in state.get("outputs", {}).items()},
            path=state.get_execution_flow(),
            message_tags=_format_tags(state.get("output_message_tags", [])),
            session_tags=_format_tags(state.get("session_tags", [])),
            participant_data_diff=participant_data_diff,
            llm_calls=[_call(span, state) for span in spans if span.type == "llm"],
            tool_calls=[_call(span, state) for span in spans if span.type == "tool"],
        )

    def as_dict(self) -> dict:
        return asdict(self)

    def prompt_variables(self) -> dict[str, str]:
        return {name: _cap(json.dumps(value, default=str)) for name, value in self.as_dict().items()}
