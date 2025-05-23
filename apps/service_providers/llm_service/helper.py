import json
import uuid

from langchain.agents.output_parsers.tools import ToolAgentAction, parse_ai_message_to_tool_action
from langchain_core.agents import AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage

original_parse = parse_ai_message_to_tool_action


def custom_parse_ai_message(message):
    if not isinstance(message, AIMessage):
        raise ValueError("Expected an AIMessage")
    if getattr(message, "tool_calls", None):
        tool_calls = [process_tool_call(tc) for tc in message.tool_calls]
    elif message.additional_kwargs.get("tool_calls"):
        tool_calls = [
            process_tool_call(tc, from_additional_kwargs=True) for tc in message.additional_kwargs["tool_calls"]
        ]
    else:
        return AgentFinish(return_values={"output": message.content}, log=str(message.content))

    actions = []
    for tool_call in tool_calls:
        args = tool_call["args"]
        tool_input = args["__arg1"] if isinstance(args, dict) and "__arg1" in args else args
        tool_call_id = tool_call["id"]
        log = f"Invoking: {tool_call['name']} with {tool_input}"

        actions.append(
            ToolAgentAction(
                tool=tool_call["name"],
                tool_input=tool_input,
                log=log,
                message_log=[message],
                tool_call_id=tool_call_id,
            )
        )

    return actions


def process_tool_call(tc, *, from_additional_kwargs=False):
    if from_additional_kwargs:
        function = tc.get("function", {})
        name = function.get("name", "")
        arguments = function.get("arguments", "{}")
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            raise OutputParserException(f"Invalid JSON for tool arguments: {arguments}")
    else:
        name = tc.get("name") or tc.get("tool") or ""
        args = tc.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                raise Exception(f"Invalid JSON for tool arguments: {args}")

    tool_call_id = tc.get("id") or str(uuid.uuid4())

    return {
        "name": name,
        "args": args,
        "id": tool_call_id,
    }
