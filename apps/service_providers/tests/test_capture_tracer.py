from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel

from apps.service_providers.tracing.base import ServiceNotInitializedException, TraceContext
from apps.service_providers.tracing.capture_tracer import CaptureCallbackHandler, CapturedSpan, CaptureTracer


def _llm_result(text: str) -> LLMResult:
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content=text))]])


def test_records_a_chat_model_call_with_its_node():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_chat_model_start(
        {}, [[HumanMessage(content="hi")]], run_id=run_id, name="FakeLlm", metadata={"langgraph_node": "llm-1"}
    )
    handler.on_llm_end(_llm_result("hello"), run_id=run_id)
    assert handler.spans == [
        CapturedSpan(
            type="llm", name="FakeLlm", node_id="llm-1", input=[{"type": "human", "content": "hi"}], output="hello"
        )
    ]


def test_records_a_tool_call():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start(
        {"name": "file-search"},
        "weather",
        run_id=run_id,
        inputs={"query": "weather"},
        metadata={"langgraph_node": "llm-1"},
    )
    handler.on_tool_end(ToolMessage(content="chunk a", tool_call_id="c1"), run_id=run_id)
    assert handler.spans == [
        CapturedSpan(type="tool", name="file-search", node_id="llm-1", input={"query": "weather"}, output="chunk a")
    ]


def test_tool_name_falls_back_to_the_serialized_name():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start({"name": "file-search"}, "weather", run_id=run_id)
    handler.on_tool_end("chunks", run_id=run_id)
    assert handler.spans == [
        CapturedSpan(type="tool", name="file-search", node_id=None, input="weather", output="chunks")
    ]


def test_a_call_inside_an_agent_is_attributed_to_the_pipeline_node():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start(
        {"name": "calculator"},
        "2+2",
        run_id=run_id,
        metadata={"langgraph_node": "tools", "langgraph_checkpoint_ns": "llm-1:task-a|tools:task-b"},
    )
    handler.on_tool_end("4", run_id=run_id)
    assert handler.spans[0].node_id == "llm-1"


class _Hit(BaseModel):
    title: str
    score: int


def test_a_model_returned_by_a_tool_is_recorded_as_plain_data():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start({"name": "search"}, "q", run_id=run_id)
    handler.on_tool_end(_Hit(title="a", score=2), run_id=run_id)
    assert handler.spans[0].output == {"title": "a", "score": 2}


def test_a_model_passed_to_a_tool_is_recorded_as_plain_data():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start({"name": "search"}, "q", run_id=run_id, inputs={"query": _Hit(title="a", score=2)})
    handler.on_tool_end("done", run_id=run_id)
    assert handler.spans[0].input == {"query": {"title": "a", "score": 2}}


def test_string_prompts_are_the_input_of_a_plain_llm_call():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_llm_start({"name": "FakeLlm"}, ["say hi"], run_id=run_id)
    handler.on_llm_end(_llm_result("hi"), run_id=run_id)
    assert handler.spans == [CapturedSpan(type="llm", name="FakeLlm", node_id=None, input=["say hi"], output="hi")]


def test_interleaved_calls_are_attributed_by_run_id():
    handler = CaptureCallbackHandler()
    first, second = uuid4(), uuid4()
    handler.on_tool_start({"name": "first"}, "a", run_id=first)
    handler.on_tool_start({"name": "second"}, "b", run_id=second)
    handler.on_tool_end("B", run_id=second)
    handler.on_tool_end("A", run_id=first)
    assert [(span.name, span.output) for span in handler.spans] == [("second", "B"), ("first", "A")]


def test_a_tool_error_is_recorded_on_the_span():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_tool_start({"name": "file-search"}, "weather", run_id=run_id)
    handler.on_tool_error(ValueError("index offline"), run_id=run_id)
    assert handler.spans == [
        CapturedSpan(type="tool", name="file-search", node_id=None, input="weather", error="index offline")
    ]


def test_an_llm_error_is_recorded_on_the_span():
    handler = CaptureCallbackHandler()
    run_id = uuid4()
    handler.on_llm_start({"name": "FakeLlm"}, ["hi"], run_id=run_id)
    handler.on_llm_error(RuntimeError(), run_id=run_id)
    assert handler.spans[0].error == "RuntimeError"


def test_an_end_without_a_start_is_ignored():
    handler = CaptureCallbackHandler()
    handler.on_llm_end(_llm_result("x"), run_id=uuid4())
    handler.on_tool_error(ValueError("x"), run_id=uuid4())
    assert handler.spans == []


def test_spans_survive_the_trace_closing():
    tracer = CaptureTracer()
    run_id = uuid4()
    with tracer.trace(TraceContext(id=uuid4(), name="eval"), session=None):
        handler = tracer.get_langchain_callback()
        handler.on_tool_start({"name": "file-search"}, "weather", run_id=run_id)
        handler.on_tool_end("chunks", run_id=run_id)
        assert tracer.ready is True
    assert tracer.ready is False
    assert [span.name for span in tracer.spans] == ["file-search"]


def test_no_callback_outside_a_trace():
    tracer = CaptureTracer()
    assert tracer.spans == []
    with pytest.raises(ServiceNotInitializedException):
        tracer.get_langchain_callback()
