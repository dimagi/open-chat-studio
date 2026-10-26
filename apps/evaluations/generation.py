from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from apps.evaluations.const import GENERATION_PROMPT_VARIABLE_MAX_LENGTH
from apps.evaluations.exceptions import EvaluationRunException
from apps.pipelines.nodes.base import PipelineState

if TYPE_CHECKING:
    from apps.service_providers.tracing.capture_tracer import CapturedSpan

TRUNCATION_SUFFIX = " [truncated]"

SpanType = Literal["reply", "node", "tool", "llm"]
SPAN_KEYS = ("type", "node", "name", "input", "output", "error")


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


def _stringify_keys(value: Any) -> Any:
    """Copy nested dicts and lists with every non-string dict key replaced by its string form."""
    if isinstance(value, dict):
        return {key if isinstance(key, str) else str(key): _stringify_keys(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_stringify_keys(item) for item in value]
    return value


@dataclass(frozen=True)
class GenerationRecord:
    """What a bot generation did, for evaluators to judge beside the reply text.

    `llm_calls` and `tool_calls` list calls in the order they started, which for a model's
    parallel tool calls is the order the model requested them. A node LangGraph retried is
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
        """The record as JSON types only: tuples become lists and other values their string form."""
        return json.loads(json.dumps(_stringify_keys(asdict(self)), default=str))

    def prompt_variables(self) -> dict[str, str]:
        return {name: _cap(json.dumps(value, ensure_ascii=False)) for name, value in self.as_dict().items()}

    def select_spans(self, span_type: SpanType, span_name: str) -> list[dict]:
        """Spans in run order: a tool by tool name, an LLM call by its node, a node by its name; none for a reply."""
        if span_type == "reply":
            return []
        spans = self._spans_of_type(span_type)
        matched_key = "node" if span_type == "llm" else "name"
        matches = [span for span in spans if span[matched_key] == span_name]
        if matches:
            return matches
        available = ", ".join(sorted({str(span[matched_key]) for span in spans})) or "none"
        raise EvaluationRunException(
            f"No {span_type} span named '{span_name}' in this generation. Available {span_type} spans: {available}"
        )

    def _spans_of_type(self, span_type: SpanType) -> list[dict]:
        """Spans built from `as_dict`, so an evaluator that changes one cannot change the record."""
        data = self.as_dict()
        if span_type == "tool":
            return [{"type": "tool", **call} for call in data["tool_calls"]]
        if span_type == "llm":
            return [{"type": "llm", **call} for call in data["llm_calls"]]
        return _node_spans(data["node_outputs"], data["path"])


def _node_spans(node_outputs: dict[str, Any], path: list[list]) -> list[dict]:
    """One span per node run, its input the output of the run that fed it.

    Pairs runs as ``PipelineState.get_node_inputs`` does: each run of a node queues its output on
    every edge it targets, and a run of the target consumes the head of its incoming edge's queue.
    """
    runs: dict[str, int] = {}
    pending: dict[tuple[str, str], deque] = defaultdict(deque)
    spans = []
    for previous, current, targets in path:
        if current is None:
            continue
        incoming = pending[(previous, current)] if previous is not None else deque()
        node_input = incoming.popleft() if incoming else None
        if current not in node_outputs:
            continue
        output = node_outputs[current]
        run = runs.get(current, 0)
        runs[current] = run + 1
        if isinstance(output, list):
            if run >= len(output):
                continue
            output = output[run]
        elif run > 0:
            continue
        spans.append(
            {
                "type": "node",
                "node": current,
                "name": current,
                "input": node_input,
                "output": output,
                "error": None,
            }
        )
        for target in targets:
            pending[(current, target)].append(output)
    return spans


SPAN_PROMPT_KEYS = ("input", "output", "error")


def _render(value: Any) -> str:
    if value is None:
        return ""
    return _cap(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def span_prompt_variables(spans: list[dict]) -> dict[str, str]:
    """The `input`, `output` and `error` prompt variables for the selected spans."""
    if not spans:
        return dict.fromkeys(SPAN_PROMPT_KEYS, "")
    if len(spans) == 1:
        return {key: _render(spans[0][key]) for key in SPAN_PROMPT_KEYS}
    return {key: _cap(json.dumps([span[key] for span in spans], ensure_ascii=False)) for key in SPAN_PROMPT_KEYS}


def span_kwarg(spans: list[dict]) -> dict | list[dict] | None:
    """The selected spans as an evaluator kwarg: None, the one span, or the list."""
    if not spans:
        return None
    if len(spans) == 1:
        return spans[0]
    return spans


def judged_response(spans: list[dict], generated_response: str) -> str:
    """The last selected span's output as text, or the generated response when nothing is selected.

    Raises `EvaluationRunException` when that span failed, since it has no output to judge.
    """
    if not spans:
        return generated_response
    span = spans[-1]
    if span["error"]:
        raise EvaluationRunException(f"Span '{span['name']}' ({span['type']}) failed: {span['error']}")
    return _render(span["output"])
