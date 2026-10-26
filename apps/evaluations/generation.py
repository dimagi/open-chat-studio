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


def _node_name(node_id: str | None, node_names: dict[str, str], state: PipelineState | None) -> str | None:
    if node_id is None:
        return None
    return node_names.get(node_id) or (state.get_node_name(node_id) if state else None) or node_id


def _call(span: CapturedSpan, node_names: dict[str, str], state: PipelineState | None) -> dict:
    return {
        "node": _node_name(span.node_id, node_names, state),
        "name": span.name,
        "input": span.input,
        "output": span.output,
        "error": span.error,
    }


def _cap(value: str) -> str:
    if len(value) <= GENERATION_PROMPT_VARIABLE_MAX_LENGTH:
        return value
    return value[:GENERATION_PROMPT_VARIABLE_MAX_LENGTH] + TRUNCATION_SUFFIX


@dataclass(frozen=True)
class GenerationRecord:
    """What a bot generation did, for evaluators to judge beside the reply text.

    `llm_calls` and `tool_calls` list calls in completion order. A node LangGraph retried is
    recorded once per attempt. Calls made outside a pipeline node, such as the reply phrased
    for an error, are left out.
    """

    node_outputs: dict[str, Any] = field(default_factory=dict)
    path: list[tuple[str | None, str | None, list[str | None]]] = field(default_factory=list)
    message_tags: list[str] = field(default_factory=list)
    session_tags: list[str] = field(default_factory=list)
    participant_data_diff: list = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)

    @classmethod
    def from_run(
        cls,
        pipeline_state: dict | None,
        participant_data_diff: list,
        spans: list[CapturedSpan],
        *,
        node_names: dict[str, str] | None = None,
    ) -> GenerationRecord:
        names = node_names or {}
        state = PipelineState(pipeline_state) if pipeline_state is not None else None
        node_spans = [span for span in spans if span.node_id is not None]
        calls = {
            "llm_calls": [_call(span, names, state) for span in node_spans if span.type == "llm"],
            "tool_calls": [_call(span, names, state) for span in node_spans if span.type == "tool"],
        }
        if state is None:
            return cls(participant_data_diff=participant_data_diff, **calls)
        return cls(
            node_outputs={name: _node_output(entry) for name, entry in state.get("outputs", {}).items()},
            path=[
                (
                    _node_name(previous, names, state),
                    _node_name(current, names, state),
                    [_node_name(target, names, state) for target in targets],
                )
                for previous, current, targets in state.get("path", [])
            ],
            message_tags=_format_tags(state.get("output_message_tags", [])),
            session_tags=_format_tags(state.get("session_tags", [])),
            participant_data_diff=participant_data_diff,
            **calls,
        )

    def as_dict(self) -> dict:
        return asdict(self)

    def prompt_variables(self) -> dict[str, str]:
        return {
            name: _cap(json.dumps(value, default=str, ensure_ascii=False)) for name, value in self.as_dict().items()
        }
