"""Builders shared by the tests that run an LLM evaluator against a fake LLM service."""

from langchain_core.messages import AIMessage

OUTPUT_SCHEMA = {"result": {"type": "string", "description": "result"}}


def structured_response():
    return AIMessage(content="", tool_calls=[{"name": "DynamicModel", "args": {"result": "ok"}, "id": "call_1"}])


def prompt_sent_to(service) -> str:
    return "\n".join(message.content for message in service.llm.get_call_messages()[0])
