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
