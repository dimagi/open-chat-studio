import uuid

from langchain.agents.output_parsers.tools import ToolAgentAction, parse_ai_message_to_tool_action
from langchain_core.agents import AgentFinish

original_parse = parse_ai_message_to_tool_action


def custom_parse_ai_message(message):
    try:
        return original_parse(message)

    except Exception as e:
        if "tool_call_id" in str(e) and "NoneType" in str(e):
            tool_calls = message.additional_kwargs.get("tool_calls", [])

            if tool_calls:
                fixed_actions = []
                for call in tool_calls:
                    function = call.get("function", {})
                    function_name = function.get("name", "")
                    try:
                        import json

                        args = json.loads(function.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    fixed_actions.append(
                        ToolAgentAction(
                            tool=function_name,
                            tool_input=args,
                            log=f"Invoking: {function_name} with {args}",
                            message_log=[message],
                            tool_call_id=f"claude_tool_{uuid.uuid4()}",
                        )
                    )
                return fixed_actions

            # No tool calls treat it as final response
            return AgentFinish(return_values={"output": message.content}, log=str(message.content))
        else:
            raise


def parse_output_for_anthropic(output):
    if output is None or isinstance(output, str):
        return output or ""

    if isinstance(output, dict):
        if "output" in output:
            return parse_output_for_anthropic(output["output"])
        elif "text" in output:
            return output.get("text", "")
        else:
            return str(output)

    if isinstance(output, list):
        result = []
        for item in output:
            if not isinstance(item, (dict | str)):
                continue

            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")

                for citation in item.get("citations", []):
                    if citation.get("title") and citation.get("url"):
                        text += f" [{citation['title']}]({citation['url']})"
                result.append(text)
            elif isinstance(item, str):
                result.append(item)
        combined_text = "".join(result)
        return combined_text

    return str(output)
