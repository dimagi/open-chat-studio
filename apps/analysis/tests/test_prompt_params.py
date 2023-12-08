import pytest

from apps.analysis.steps.processors import PromptParams


def test_prompt_params_returns_correct_template():
    params = PromptParams(prompt="Hello, {data}!")
    assert params.prompt_template.template == "Hello, {data}!"


def test_prompt_params_raises_error_with_missing_data_variable():
    with pytest.raises(ValueError):
        PromptParams(prompt="Hello, {name}!")


def test_prompt_params_raises_error_with_extra_variables():
    with pytest.raises(ValueError):
        PromptParams(prompt="Hello, {data} and {name}!")
