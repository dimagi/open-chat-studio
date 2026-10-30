import json
from datetime import datetime

import pytest
from pydantic import BaseModel

from apps.evaluations.const import GENERATION_PROMPT_VARIABLE_MAX_LENGTH
from apps.evaluations.generation import GenerationRecord
from apps.service_providers.tracing.capture_tracer import CapturedSpan

STATE = {
    "outputs": {
        "start": {"message": "hi", "node_id": "start-1"},
        "Tagger": {"message": "tagged: hi", "node_id": "code-1"},
    },
    "path": [(None, "start-1", ["code-1"]), ("start-1", "code-1", [])],
    "output_message_tags": [("urgent", ""), ("blocked", "safety_layer_response")],
    "session_tags": [("needs-review", "")],
}


def test_from_run_reads_the_final_state():
    record = GenerationRecord.from_run(STATE, participant_data_diff=[], spans=[])
    assert record.node_outputs == {"start": "hi", "Tagger": "tagged: hi"}
    assert record.path == [(None, "start", ["Tagger"]), ("start", "Tagger", [])]
    assert record.message_tags == ["urgent", "safety_layer_response:blocked"]
    assert record.session_tags == ["needs-review"]


def test_a_node_that_ran_twice_keeps_both_outputs():
    state = {"outputs": {"loop": [{"message": "a", "node_id": "l-1"}, {"message": "b", "node_id": "l-1"}]}}
    assert GenerationRecord.from_run(state, [], []).node_outputs == {"loop": ["a", "b"]}


def test_spans_split_into_llm_and_tool_calls_named_by_node():
    spans = [
        CapturedSpan(
            type="llm", name="FakeLlm", node_id="code-1", input=[{"type": "human", "content": "hi"}], output="ok"
        ),
        CapturedSpan(type="tool", name="file-search", node_id="code-1", input={"query": "q"}, output="chunks"),
        CapturedSpan(type="tool", name="orphan", node_id="gone-9", input="x", output="y"),
    ]
    record = GenerationRecord.from_run(STATE, [], spans)
    assert record.llm_calls == [
        {
            "node": "Tagger",
            "name": "FakeLlm",
            "input": [{"type": "human", "content": "hi"}],
            "output": "ok",
            "error": None,
        }
    ]
    assert record.tool_calls == [
        {"node": "Tagger", "name": "file-search", "input": {"query": "q"}, "output": "chunks", "error": None},
        {"node": "gone-9", "name": "orphan", "input": "x", "output": "y", "error": None},
    ]


def test_node_names_resolve_from_the_map_before_the_state():
    spans = [
        CapturedSpan(type="llm", name="FakeLlm", node_id="llm-2", input="hi", output=""),
        CapturedSpan(type="tool", name="calc", node_id="code-1", input="x", output="y"),
    ]
    state = {**STATE, "path": [(None, "start-1", ["code-1"]), ("start-1", "code-1", ["llm-2"])]}
    record = GenerationRecord.from_run(
        state, [], spans, node_names={"llm-2": "Second", "code-1": "Renamed", "start-1": "start"}
    )
    assert record.llm_calls[0]["node"] == "Second"
    assert record.tool_calls[0]["node"] == "Renamed"
    assert record.path == [(None, "start", ["Renamed"]), ("start", "Renamed", ["Second"])]


def test_calls_made_outside_a_pipeline_node_are_left_out():
    spans = [
        CapturedSpan(type="llm", name="FakeLlm", node_id="code-1", input="hi", output="ok"),
        CapturedSpan(type="llm", name="EventBot", node_id=None, input="phrase the error", output="Sorry"),
        CapturedSpan(type="tool", name="calc", node_id=None, input="x", output="y"),
    ]
    record = GenerationRecord.from_run(STATE, [], spans)
    assert [call["name"] for call in record.llm_calls] == ["FakeLlm"]
    assert record.tool_calls == []


def test_the_diff_is_carried_as_given():
    diff = [("change", "plan", ("free", "pro"))]
    assert GenerationRecord.from_run({}, diff, []).participant_data_diff == diff


def test_no_state_means_an_empty_record():
    assert GenerationRecord.from_run(None, [], []) == GenerationRecord()


def test_spans_are_kept_when_the_run_left_no_state():
    spans = [CapturedSpan(type="tool", name="file-search", node_id="llm-1", input={"query": "q"}, output="chunks")]
    record = GenerationRecord.from_run(None, [], spans)
    assert record.tool_calls == [
        {"node": "llm-1", "name": "file-search", "input": {"query": "q"}, "output": "chunks", "error": None}
    ]
    assert record.llm_calls == []
    assert record.node_outputs == {}
    assert record.path == []
    assert record.message_tags == []
    assert record.session_tags == []


def test_prompt_variables_are_json_strings_for_every_field():
    variables = GenerationRecord(message_tags=["urgent"]).prompt_variables()
    assert set(variables) == {
        "node_outputs",
        "path",
        "message_tags",
        "session_tags",
        "participant_data_diff",
        "llm_calls",
        "tool_calls",
    }
    assert variables["message_tags"] == '["urgent"]'
    assert variables["node_outputs"] == "{}"
    assert variables["tool_calls"] == "[]"


@pytest.mark.parametrize(
    ("length", "expected_length", "truncated"),
    [
        pytest.param(
            GENERATION_PROMPT_VARIABLE_MAX_LENGTH, GENERATION_PROMPT_VARIABLE_MAX_LENGTH, False, id="at-the-cap"
        ),
        pytest.param(
            GENERATION_PROMPT_VARIABLE_MAX_LENGTH + 1,
            GENERATION_PROMPT_VARIABLE_MAX_LENGTH + len(" [truncated]"),
            True,
            id="one-over-the-cap",
        ),
    ],
)
def test_prompt_variables_are_capped(length, expected_length, truncated):
    record = GenerationRecord(message_tags=["x" * (length - len('[""]'))])
    value = record.prompt_variables()["message_tags"]
    assert len(value) == expected_length
    assert value.endswith(" [truncated]") is truncated


def test_prompt_variables_keep_non_ascii_text():
    record = GenerationRecord(node_outputs={"n": "नमस्ते 🙂"})
    value = record.prompt_variables()["node_outputs"]
    assert "नमस्ते 🙂" in value
    assert "\\u" not in value


def test_prompt_variables_tolerate_non_json_values():
    record = GenerationRecord(participant_data_diff=[("add", "", [("seen_at", datetime(2026, 10, 26))])])
    assert "2026-10-26" in record.prompt_variables()["participant_data_diff"]


class _Query(BaseModel):
    text: str


def _json_types_only(value) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_types_only(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_json_types_only(item) for item in value)
    return value is None or isinstance(value, str | int | float | bool)


def test_as_dict_is_plain_data():
    spans = [CapturedSpan(type="tool", name="search", node_id="code-1", input={"query": _Query(text="q")}, output="")]
    diff = [("add", "", [("seen_at", datetime(2026, 10, 26))])]
    record = GenerationRecord.from_run(STATE, diff, spans)

    data = record.as_dict()

    assert json.loads(json.dumps(data)) == data
    assert _json_types_only(data)
    assert data["message_tags"] == ["urgent", "safety_layer_response:blocked"]


def test_non_string_dict_keys_are_stringified():
    output = {("a", 1): "x", datetime(2026, 10, 26): "y"}
    record = GenerationRecord(tool_calls=[{"node": "n", "name": "t", "input": None, "output": output, "error": None}])

    assert record.as_dict()["tool_calls"][0]["output"] == {"('a', 1)": "x", "2026-10-26 00:00:00": "y"}
    assert isinstance(record.prompt_variables()["tool_calls"], str)
