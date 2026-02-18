from unittest import mock

import pydantic as pydantic_module
import pytest
from pydantic import BaseModel

from apps.help.agents.code_generate import CodeGenerateAgent, CodeGenerateInput, CodeGenerateOutput
from apps.help.base import BaseHelpAgent
from apps.help.registry import AGENT_REGISTRY, register_agent
from apps.help.utils import extract_function_signature, get_python_node_coder_prompt


def test_get_python_node_coder_prompt():
    current_code = "bla bla bla"
    error = "alb alb alb"
    prompt = get_python_node_coder_prompt(current_code, error)
    assert "get_participant_data" in prompt
    assert current_code in prompt
    assert error in prompt


class TestExtractFunctionSignature:
    def test_function_with_args(self):
        def func_with_args(a, b, c=10):
            """Function with arguments."""
            pass

        result = extract_function_signature("func_with_args", func_with_args)
        expected = 'def func_with_args(a, b, c=10):\n    """Function with arguments."""\n'
        assert result == expected

    def test_function_without_docstring(self):
        def no_docstring_func(x):
            return x

        result = extract_function_signature("no_docstring_func", no_docstring_func)
        expected = "def no_docstring_func(x):\n    pass\n"
        assert result == expected

    def test_function_with_multiline_docstring(self):
        def multiline_func():
            """This is a function with a multiline docstring.

            It has multiple lines.
            And provides detailed information."""
            pass

        result = extract_function_signature("multiline_func", multiline_func)
        expected = '''def multiline_func():
    """This is a function with a multiline docstring.

    It has multiple lines.
    And provides detailed information."""
'''
        assert result == expected

    def test_non_callable_object_returns_none(self):
        result = extract_function_signature("not_callable", "string")
        assert result is None


class TestAgentRegistry:
    def test_register_agent_adds_to_registry(self):
        # Use a throwaway class to avoid polluting the registry
        original_registry = AGENT_REGISTRY.copy()
        try:

            @register_agent
            class FakeAgent:
                name = "test_fake"

            assert AGENT_REGISTRY["test_fake"] is FakeAgent
        finally:
            AGENT_REGISTRY.clear()
            AGENT_REGISTRY.update(original_registry)

    def test_register_agent_returns_class_unchanged(self):
        original_registry = AGENT_REGISTRY.copy()
        try:

            @register_agent
            class AnotherFake:
                name = "test_another"

            assert AnotherFake.name == "test_another"
        finally:
            AGENT_REGISTRY.clear()
            AGENT_REGISTRY.update(original_registry)


class StubInput(BaseModel):
    prompt: str


class StubOutput(BaseModel):
    result: str


class StubAgent(BaseHelpAgent[StubInput, StubOutput]):
    name = "stub"
    mode = "low"

    @classmethod
    def get_system_prompt(cls, input):
        return "You are a stub."

    @classmethod
    def get_user_message(cls, input):
        return input.prompt

    def parse_response(self, response) -> StubOutput:
        return StubOutput(result=response["messages"][-1].text)


class TestBaseHelpAgent:
    @mock.patch("apps.help.base.build_system_agent")
    def test_run_invokes_agent_and_returns_parsed_output(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"messages": [mock.Mock(text="hello")]}
        mock_build.return_value = mock_agent

        agent = StubAgent(input=StubInput(prompt="say hi"))
        result = agent.run()

        assert result == StubOutput(result="hello")
        mock_build.assert_called_once_with("low", "You are a stub.")
        mock_agent.invoke.assert_called_once_with({"messages": [{"role": "user", "content": "say hi"}]})


class TestCodeGenerateAgent:
    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_returns_valid_code(self, mock_build):
        valid_code = "def main(input: str, **kwargs) -> str:\n    return input"
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"messages": [mock.Mock(text=valid_code)]}
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode"):
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="write hello world"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=valid_code)

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_retries_on_validation_error(self, mock_build):
        bad_code = "not valid python"
        good_code = "def main(input: str, **kwargs) -> str:\n    return input"

        mock_agent = mock.Mock()
        mock_agent.invoke.side_effect = [
            {"messages": [mock.Mock(text=bad_code)]},
            {"messages": [mock.Mock(text=good_code)]},
        ]
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode") as mock_code_node:
            mock_code_node.model_validate.side_effect = [
                pydantic_module.ValidationError.from_exception_data("CodeNode", []),
                None,
            ]
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="fix this"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=good_code)
        assert mock_agent.invoke.call_count == 2

    @mock.patch("apps.help.agents.code_generate.build_system_agent")
    def test_run_returns_last_code_after_max_retries(self, mock_build):
        bad_code = "still broken"
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"messages": [mock.Mock(text=bad_code)]}
        mock_build.return_value = mock_agent

        with mock.patch("apps.help.agents.code_generate.CodeNode") as mock_code_node:
            mock_code_node.model_validate.side_effect = pydantic_module.ValidationError.from_exception_data(
                "CodeNode", []
            )
            agent = CodeGenerateAgent(input=CodeGenerateInput(query="fix this"))
            result = agent.run()

        assert result == CodeGenerateOutput(code=bad_code)
        # 1 initial + 3 retries = 4 total
        assert mock_agent.invoke.call_count == 4

    def test_input_validates_query_required(self):
        with pytest.raises(pydantic_module.ValidationError):
            CodeGenerateInput()

    def test_input_context_defaults_to_empty(self):
        inp = CodeGenerateInput(query="hello")
        assert inp.context == ""
