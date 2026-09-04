"""Adapter classes that mediate between a pipeline node and an LLM service."""

from __future__ import annotations

from langchain_core.prompts import PromptTemplate

from apps.service_providers.llm_service.main import (
    AnthropicBuiltinTool,
    OpenAIBuiltinTool,
)


class BaseAdapter:
    ai_message = None

    def get_llm_service(self):
        return self.llm_service

    def format_input(self, input: str) -> str:
        if not self.input_formatter:
            return input

        template = PromptTemplate.from_template(self.input_formatter)
        context = self.template_context.get_context(template.input_variables)
        context["input"] = input
        return template.format(**context)

    def get_allowed_tools(self):
        if self.disabled_tools:
            # Model builtin tools doesn't have a name attribute and are dicts
            return [tool for tool in self.tools if hasattr(tool, "name") and tool.name not in self.disabled_tools]
        return self.tools

    def get_callable_tools(self):
        """Filter out tools that are not OCS tools. `AgentExecutor` expects a list of runnable tools, so we need to
        remove all tools that are run by the LLM provider
        """
        from google.ai.generativelanguage_v1beta.types import (  # noqa: PLC0415 - lazy: optional Google AI lib
            Tool as GenAITool,
        )

        return [
            t
            for t in self.get_allowed_tools()
            if not isinstance(t, OpenAIBuiltinTool | GenAITool | AnthropicBuiltinTool)
        ]
