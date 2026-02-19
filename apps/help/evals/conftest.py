from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from django.conf import settings
from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.evals.checks import (
    check_code_node,
    check_count,
    check_execute,
    check_filter_params,
    check_has_main,
    check_max_words,
    check_syntax,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

JUDGE_SYSTEM_PROMPT = (
    "You are an evaluation judge. Assess whether the given output meets the stated criteria.\n"
    "Be strict: only pass if the output clearly meets the criteria."
)


class JudgeResult(BaseModel):
    passed: bool
    reason: str


def load_fixtures(path: Path) -> list[dict]:
    """Load test cases from a YAML fixture file."""
    with open(path) as f:
        return yaml.safe_load(f)


def llm_judge(output: str, criteria: str) -> tuple[bool, str]:
    """Ask an LLM whether the output meets the criteria.
    Returns (passed, reason).
    """
    agent = build_system_agent("low", JUDGE_SYSTEM_PROMPT, response_format=JudgeResult)
    response = agent.invoke({"messages": [{"role": "user", "content": f"Criteria: {criteria}\n\nOutput:\n{output}"}]})
    result: JudgeResult = response["structured_response"]
    return result.passed, result.reason


# Map check types to functions. Each returns None on success, error string on failure.
CHECK_DISPATCH: dict[str, Callable] = {
    "syntax": lambda output, _params: check_syntax(output),
    "has_main": lambda output, _params: check_has_main(output),
    "code_node": lambda output, _params: check_code_node(output),
    "execute": lambda output, params: check_execute(output, params["input"], params["expected"]),
    "count": lambda output, params: check_count(output, params["expected"]),
    "max_words": lambda output, params: check_max_words(output, params["per_message"]),
    "filter_params": lambda output, params: check_filter_params(output, params["expected"]),
}


def run_checks(output, checks: list[dict], output_field: str | None = None):
    """Run all checks against the output. Raises AssertionError with all failures."""
    # If output is a Pydantic model, extract the relevant field
    if output_field and hasattr(output, output_field):
        raw_output = getattr(output, output_field)
    else:
        raw_output = output

    failures = []
    for check in checks:
        check_type = check["type"]
        params = {k: v for k, v in check.items() if k != "type"}

        if check_type == "llm_judge":
            passed, reason = llm_judge(str(raw_output), params["criteria"])
            if not passed:
                failures.append(f"llm_judge: {reason}")
        elif check_type in CHECK_DISPATCH:
            error = CHECK_DISPATCH[check_type](raw_output, params)
            if error:
                failures.append(f"{check_type}: {error}")
        else:
            failures.append(f"Unknown check type: {check_type}")

    if failures:
        raise AssertionError("Eval check failures:\n" + "\n".join(f"  - {f}" for f in failures))


def _system_agent_models_configured() -> bool:
    """Check if system agent API keys are configured.

    Both HIGH and LOW tiers are required: agents use HIGH for generation,
    and llm_judge uses LOW for evaluation.
    """
    return bool(getattr(settings, "SYSTEM_AGENT_MODELS_HIGH", None)) and bool(
        getattr(settings, "SYSTEM_AGENT_MODELS_LOW", None)
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip eval tests if system agent models are not configured."""
    skip_eval = pytest.mark.skip(reason="System agent models not configured (need API keys)")
    for item in items:
        if "eval" in item.keywords and not _system_agent_models_configured():
            item.add_marker(skip_eval)
