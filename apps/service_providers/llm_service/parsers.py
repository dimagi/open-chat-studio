import json
from json import JSONDecodeError

from langchain_classic.agents.output_parsers.tools import ToolAgentAction, parse_ai_message_to_tool_action
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, ToolCall

from apps.experiments.models import ExperimentSession
from apps.service_providers.llm_service.datamodels import LlmChatResponse

original_parse = parse_ai_message_to_tool_action


def custom_parse_ai_message(message) -> list[AgentAction] | AgentFinish:
    """Parse an AI message potentially containing tool_calls.
    This function is adapted from the `parse_ai_message_to_tool_action`
    function in the langchain library.
    1. Added a specific condition to skip malformed built in tool calls that
       originate from Anthropic models (where `call.get("type") == "tool_call"`,
       `call.get("name") == ""`, and `call.get("id") is None`).

    This function is almost and exact copy of `langchain.agents.output_parsers.tools.parse_ai_message_to_tool_action`
    with the exception of the above condition.
    """
    if not isinstance(message, AIMessage):
        raise TypeError(f"Expected an AI message got {type(message)}")

    actions: list = []
    if message.tool_calls:
        tool_calls = message.tool_calls
    else:
        if not message.additional_kwargs.get("tool_calls"):
            return AgentFinish(return_values={"output": message.content}, log=str(message.content))
        # Best-effort parsing
        tool_calls = []
        for tool_call in message.additional_kwargs["tool_calls"]:
            function = tool_call["function"]
            function_name = function["name"]
            try:
                args = json.loads(function["arguments"] or "{}")
                tool_calls.append(ToolCall(name=function_name, args=args, id=tool_call["id"]))
            except JSONDecodeError:
                raise OutputParserException(
                    f"Could not parse tool input: {function} because the `arguments` is not valid JSON."
                ) from None

    for tool_call in tool_calls:
        # HACK HACK HACK:
        # The code that encodes tool input into Open AI uses a special variable
        # name called `__arg1` to handle old style tools that do not expose a
        # schema and expect a single string argument as an input.
        # We unpack the argument here if it exists.
        # Open AI does not support passing in a JSON array as an argument.
        function_name = tool_call["name"]
        _tool_input = tool_call["args"]
        if "__arg1" in _tool_input:
            tool_input = _tool_input["__arg1"]
        else:
            tool_input = _tool_input

        # Skip malformed built-in tool calls that originate from Anthropic models
        if tool_call.get("type") == "tool_call" and not tool_call.get("name") and tool_call.get("id") is None:
            continue

        content_msg = f"responded: {message.content}\n" if message.content else "\n"
        log = f"\nInvoking: `{function_name}` with `{tool_input}`\n{content_msg}\n"
        actions.append(
            ToolAgentAction(
                tool=function_name,
                tool_input=tool_input,
                log=log,
                message_log=[message],
                tool_call_id=tool_call["id"],
            )
        )

    if not actions:
        return AgentFinish(return_values={"output": message.content}, log=str(message.content))
    return actions


def parse_output_for_anthropic(output, session: ExperimentSession, include_citations: bool = True) -> LlmChatResponse:
    if output is None or isinstance(output, str):
        return LlmChatResponse(text=output or "")

    if isinstance(output, list):
        chat_response = LlmChatResponse(text="")
        for item in output:
            chat_response += parse_output_for_anthropic(item, session=session, include_citations=include_citations)
        return chat_response

    if isinstance(output, dict):
        if "output" in output:
            return parse_output_for_anthropic(output["output"], session=session, include_citations=include_citations)
        elif output.get("type") == "text":
            text = output.get("text", "")
            for citation in output.get("citations", []):
                if citation.get("title") and citation.get("url"):
                    text += f" [{citation['title']}]({citation['url']})"
            # TODO: Add cited files
            return LlmChatResponse(text=text)
        else:
            return LlmChatResponse(text="")
    return LlmChatResponse(text="")
