import json

import pytest

from apps.evaluations.const import GENERATION_PROMPT_VARIABLE_MAX_LENGTH
from apps.evaluations.exceptions import EvaluationRunException
from apps.evaluations.generation import (
    SPAN_KEYS,
    GenerationRecord,
    judged_response,
    span_kwarg,
    span_prompt_variables,
)

SEARCH = {"node": "Answer", "name": "file-search", "input": {"query": "q"}, "output": "chunk a\nchunk b", "error": None}
CALC = {"node": "Answer", "name": "calculator", "input": {"expression": "2+2"}, "output": "4", "error": None}
ANSWER_CALL = {
    "node": "Answer",
    "name": "FakeLlm",
    "input": [{"type": "human", "content": "hi"}],
    "output": "reply",
    "error": None,
}


@pytest.fixture()
def record():
    return GenerationRecord(
        node_outputs={"start": "hello", "Answer": "reply", "Loop": ["a", "b"]},
        path=[
            (None, "start", ["Answer"]),
            ("start", "Answer", ["Loop"]),
            ("Answer", "Loop", ["Loop"]),
            ("Loop", "Loop", ["end"]),
        ],
        tool_calls=[SEARCH, CALC],
        llm_calls=[ANSWER_CALL],
    )


def test_reply_selects_nothing(record):
    assert record.select_spans("reply", "") == []


def test_a_tool_span_matches_by_tool_name(record):
    assert record.select_spans("tool", "file-search") == [{"type": "tool", **SEARCH}]


def test_an_llm_span_matches_by_node_name(record):
    assert record.select_spans("llm", "Answer") == [{"type": "llm", **ANSWER_CALL}]


def test_a_node_span_pairs_its_output_with_the_previous_nodes_output(record):
    assert record.select_spans("node", "Answer") == [
        {"type": "node", "node": "Answer", "name": "Answer", "input": "hello", "output": "reply", "error": None}
    ]


def test_the_first_node_has_no_input(record):
    assert record.select_spans("node", "start")[0]["input"] is None


def test_a_node_run_twice_gives_one_span_per_run(record):
    assert [(span["input"], span["output"]) for span in record.select_spans("node", "Loop")] == [
        ("reply", "a"),
        ("a", "b"),
    ]


@pytest.mark.parametrize(
    ("span_type", "span_name"),
    [
        pytest.param("node", "Answer", id="node"),
        pytest.param("tool", "calculator", id="tool"),
        pytest.param("llm", "Answer", id="llm"),
    ],
)
def test_every_span_has_exactly_the_span_keys(record, span_type, span_name):
    for span in record.select_spans(span_type, span_name):
        assert set(span) == set(SPAN_KEYS)


def test_a_node_with_an_output_but_no_run_has_no_span():
    record = GenerationRecord(node_outputs={"start": "s", "Ghost": "g"}, path=[(None, "start", [])])
    with pytest.raises(EvaluationRunException, match=r"Available node spans: start$"):
        record.select_spans("node", "Ghost")


def test_a_node_run_with_no_output_has_no_span():
    record = GenerationRecord(node_outputs={"start": "s"}, path=[(None, "start", ["Ghost"]), ("start", "Ghost", [])])
    with pytest.raises(EvaluationRunException, match=r"Available node spans: start$"):
        record.select_spans("node", "Ghost")


def test_runs_beyond_a_list_output_have_no_span():
    record = GenerationRecord(
        node_outputs={"Loop": ["a", "b"]},
        path=[(None, "Loop", ["Loop"]), ("Loop", "Loop", ["Loop"]), ("Loop", "Loop", ["end"])],
    )
    assert [span["output"] for span in record.select_spans("node", "Loop")] == ["a", "b"]


def test_a_path_entry_with_no_current_node_has_no_span():
    record = GenerationRecord(node_outputs={"start": "s"}, path=[(None, "start", []), ("start", None, [])])
    assert [span["output"] for span in record.select_spans("node", "start")] == ["s"]


def test_a_second_run_of_a_node_with_a_single_output_has_no_span():
    record = GenerationRecord(node_outputs={"start": "s"}, path=[(None, "start", ["start"]), ("start", "start", [])])
    assert [span["output"] for span in record.select_spans("node", "start")] == ["s"]


def test_a_successor_run_pairs_with_the_run_that_fed_it():
    record = GenerationRecord(
        node_outputs={
            "start": "s",
            "A": "a",
            "B": "b",
            "B2": "b2",
            "M": ["m-a", "m-b"],
            "end": ["m-a", "m-b"],
        },
        path=[
            (None, "start", ["A", "B"]),
            ("start", "A", ["M"]),
            ("start", "B", ["B2"]),
            ("A", "M", ["end"]),
            ("B2", "M", ["end"]),
            ("M", "end", []),
            ("M", "end", []),
        ],
    )
    assert [(span["input"], span["output"]) for span in record.select_spans("node", "end")] == [
        ("m-a", "m-a"),
        ("m-b", "m-b"),
    ]


def test_a_branch_node_takes_its_own_predecessors_output():
    record = GenerationRecord(
        node_outputs={"X": "x", "A": "a", "B": "b"},
        path=[(None, "X", ["A", "B"]), ("X", "A", []), ("X", "B", [])],
    )
    assert record.select_spans("node", "B")[0]["input"] == "x"


@pytest.mark.parametrize(
    ("span_type", "span_name", "message"),
    [
        pytest.param(
            "tool",
            "search",
            "No tool span named 'search' in this generation. Available tool spans: calculator, file-search",
            id="tool",
        ),
        pytest.param(
            "llm", "Guard", "No llm span named 'Guard' in this generation. Available llm spans: Answer", id="llm"
        ),
        pytest.param(
            "node",
            "Guard",
            "No node span named 'Guard' in this generation. Available node spans: Answer, Loop, start",
            id="node",
        ),
    ],
)
def test_no_match_names_the_available_spans(record, span_type, span_name, message):
    with pytest.raises(EvaluationRunException, match=message):
        record.select_spans(span_type, span_name)


def test_an_empty_record_reports_no_spans_of_that_type():
    with pytest.raises(EvaluationRunException, match="Available tool spans: none"):
        GenerationRecord().select_spans("tool", "file-search")


def test_prompt_variables_render_strings_raw_and_other_values_as_json(record):
    assert span_prompt_variables(record.select_spans("tool", "file-search")) == {
        "input": '{"query": "q"}',
        "output": "chunk a\nchunk b",
        "error": "",
    }


def _tool_span(**fields):
    return {"type": "tool", "node": "n", "name": "t", "input": "x", "output": "y", "error": None, **fields}


@pytest.mark.parametrize(
    ("output", "rendered"),
    [
        pytest.param(None, "", id="none"),
        pytest.param({"answer": 4}, '{"answer": 4}', id="dict-as-json"),
        pytest.param({"städte": "Zürich"}, '{"städte": "Zürich"}', id="non-ascii-kept"),
    ],
)
def test_a_single_span_output_renders(output, rendered):
    assert span_prompt_variables([_tool_span(output=output)])["output"] == rendered


def test_prompt_variables_for_several_spans_are_json_lists(record):
    variables = span_prompt_variables(record.select_spans("node", "Loop"))
    assert json.loads(variables["input"]) == ["reply", "a"]
    assert json.loads(variables["output"]) == ["a", "b"]


def test_prompt_variables_without_a_selector_are_empty():
    assert span_prompt_variables([]) == {"input": "", "output": "", "error": ""}


def test_prompt_variables_include_the_error():
    assert span_prompt_variables([_tool_span(error="boom")])["error"] == "boom"
    assert json.loads(span_prompt_variables([_tool_span(), _tool_span(error="boom")])["error"]) == [None, "boom"]


def test_judged_response_raises_for_an_errored_span():
    with pytest.raises(EvaluationRunException, match=r"Span 't' \(tool\) failed: boom"):
        judged_response([_tool_span(error="boom")], "original")


def test_judged_response_renders_a_dict_output_as_json():
    assert judged_response([_tool_span(output={"answer": 4})], "original") == '{"answer": 4}'


@pytest.mark.parametrize("key", ["input", "output"])
@pytest.mark.parametrize("span_count", [pytest.param(1, id="single"), pytest.param(2, id="several")])
def test_prompt_variables_are_capped(key, span_count):
    long_span = _tool_span(**{key: "y" * (GENERATION_PROMPT_VARIABLE_MAX_LENGTH + 1)})
    variables = span_prompt_variables([long_span] * span_count)
    assert variables[key].endswith(" [truncated]")
    assert len(variables[key]) == GENERATION_PROMPT_VARIABLE_MAX_LENGTH + len(" [truncated]")


@pytest.mark.parametrize(
    ("span_type", "span_name", "expected"),
    [
        pytest.param("reply", "", None, id="none-without-a-selector"),
        pytest.param("tool", "calculator", {"type": "tool", **CALC}, id="dict-for-one-span"),
        pytest.param(
            "node",
            "Loop",
            [
                {"type": "node", "node": "Loop", "name": "Loop", "input": "reply", "output": "a", "error": None},
                {"type": "node", "node": "Loop", "name": "Loop", "input": "a", "output": "b", "error": None},
            ],
            id="list-for-several-spans",
        ),
    ],
)
def test_span_kwarg_is_none_a_dict_or_a_list(record, span_type, span_name, expected):
    assert span_kwarg(record.select_spans(span_type, span_name)) == expected


@pytest.mark.parametrize(
    ("span_type", "span_name", "expected"),
    [
        pytest.param("node", "Loop", "b", id="last-of-several"),
        pytest.param("tool", "calculator", "4", id="single"),
        pytest.param("reply", "", "original", id="reply-without-a-selector"),
    ],
)
def test_judged_response_is_the_last_spans_output(record, span_type, span_name, expected):
    assert judged_response(record.select_spans(span_type, span_name), "original") == expected
