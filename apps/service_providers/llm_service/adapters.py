"""Adapter classes that mediate between a pipeline node and an LLM service."""

from __future__ import annotations

from langchain_core.prompts import PromptTemplate

from apps.service_providers.llm_service.utils import exclude_disabled_tools


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
        return exclude_disabled_tools(self.tools, self.disabled_tools)
