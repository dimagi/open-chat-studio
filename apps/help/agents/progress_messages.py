from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent

PROGRESS_MESSAGE_PROMPT = """\
You will be generating progress update messages that are displayed to users while they \
wait for a chatbot to respond. These messages should keep users engaged and informed \
during the wait time.

Here are the guidelines for creating effective progress messages:

- Keep messages SHORT - aim for 2-4 words maximum
- Use an encouraging, friendly, and slightly playful tone
- Messages should feel dynamic and suggest active work is happening
- Vary the style and wording across different messages
- Focus on the process (e.g., "thinking", "analyzing", "processing") rather than making promises about results
- Avoid technical jargon or overly complex language
- Don't make specific claims about what the answer will contain
- Don't apologize for wait times or sound negative

Generate message options that could be rotated or randomly displayed to users.
Each message should feel fresh and distinct from the others."""


class ProgressMessagesInput(BaseModel):
    chatbot_name: str
    chatbot_description: str | None = None


class ProgressMessagesOutput(BaseModel):
    messages: list[str]


@register_agent
class ProgressMessagesAgent(BaseHelpAgent[ProgressMessagesInput, ProgressMessagesOutput]):
    name: ClassVar[str] = "progress_messages"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: ProgressMessagesInput) -> str:
        return PROGRESS_MESSAGE_PROMPT

    @classmethod
    def get_user_message(cls, input: ProgressMessagesInput) -> str:
        message = f"Please generate 30 progress messages for this chatbot:\nName: '{input.chatbot_name}'"
        if input.chatbot_description:
            message += f"\nDescription: '{input.chatbot_description}'"
        return message
