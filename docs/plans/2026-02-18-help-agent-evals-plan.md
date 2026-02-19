# Help Agent Static Evals — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add static evaluations for CodeGenerateAgent and ProgressMessagesAgent that test structural correctness and output quality using real LLM calls.

**Architecture:** YAML fixtures define test cases with programmatic checks and LLM-as-judge criteria. Tests run under a `@pytest.mark.eval` marker, excluded from default `pytest` runs. A thin `llm_judge()` helper reuses the existing `build_system_agent` infrastructure.

**Tech Stack:** pytest, PyYAML (already a dependency), Pydantic, existing `build_system_agent` from `apps/help/agent.py`

**Design doc:** `docs/plans/2026-02-18-help-agent-evals-design.md`

---

### Task 1: Add `eval` pytest marker to pyproject.toml

**Files:**
- Modify: `pyproject.toml:97-103`

**Step 1: Update pyproject.toml**

In `pyproject.toml`, update the `[tool.pytest.ini_options]` section to add the `eval` marker and exclude it from default runs:

```toml
[tool.pytest.ini_options]
addopts = "--ds=config.settings --reuse-db --strict-markers --tb=short -m \"not integration and not eval\""
python_files = "tests.py test_*.py *_tests.py"
norecursedirs = ".* build dist venv node_modules compose assets static"
markers = [
    "integration: marks tests as integration tests (deselected by default)",
    "eval: marks tests as LLM evaluation tests (deselected by default, requires API keys)",
]
```

**Step 2: Verify existing tests still run**

Run: `pytest apps/help/tests.py -v --co`
Expected: All existing tests collected, no warnings about unknown markers.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add eval pytest marker, excluded from default runs"
```

---

### Task 2: Create `apps/help/evals/checks.py` with programmatic checks

**Files:**
- Create: `apps/help/evals/__init__.py`
- Create: `apps/help/evals/checks.py`
- Create: `apps/help/evals/test_checks.py`

**Step 1: Create the empty `__init__.py`**

Create `apps/help/evals/__init__.py` as an empty file.

**Step 2: Write failing tests for check functions**

Create `apps/help/evals/test_checks.py`:

```python
import pytest

from apps.help.evals.checks import (
    check_code_node,
    check_count,
    check_has_main,
    check_max_words,
    check_syntax,
)


class TestCheckSyntax:
    def test_valid_python(self):
        assert check_syntax("x = 1") is None

    def test_invalid_python(self):
        result = check_syntax("def foo(")
        assert result is not None
        assert "syntax" in result.lower() or "SyntaxError" in result


class TestCheckHasMain:
    def test_valid_main(self):
        code = "def main(input: str, **kwargs) -> str:\n    return input"
        assert check_has_main(code) is None

    def test_missing_main(self):
        code = "def foo(input: str) -> str:\n    return input"
        result = check_has_main(code)
        assert result is not None

    def test_wrong_signature(self):
        code = "def main(x: int) -> int:\n    return x"
        result = check_has_main(code)
        assert result is not None

    def test_wrong_return_type(self):
        code = "def main(input: str, **kwargs) -> int:\n    return 1"
        result = check_has_main(code)
        assert result is not None


class TestCheckCodeNode:
    def test_valid_code(self):
        code = "def main(input: str, **kwargs) -> str:\n    return input"
        assert check_code_node(code) is None

    def test_invalid_code(self):
        result = check_code_node("not valid at all")
        assert result is not None


class TestCheckCount:
    def test_correct_count(self):
        assert check_count(["a", "b", "c"], 3) is None

    def test_wrong_count(self):
        result = check_count(["a", "b"], 3)
        assert result is not None
        assert "2" in result and "3" in result


class TestCheckMaxWords:
    def test_within_limit(self):
        assert check_max_words(["two words", "three little words"], 4) is None

    def test_exceeds_limit(self):
        result = check_max_words(["this has way too many words in it"], 4)
        assert result is not None
```

**Step 3: Run tests to verify they fail**

Run: `pytest apps/help/evals/test_checks.py -v`
Expected: ImportError — `checks` module doesn't exist yet.

**Step 4: Implement check functions**

Create `apps/help/evals/checks.py`:

```python
from __future__ import annotations

import ast


def check_syntax(code: str) -> str | None:
    """Check that code is valid Python. Returns None on success, error message on failure."""
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        return f"SyntaxError: {e}"


def check_has_main(code: str) -> str | None:
    """Check that code defines `def main(input: str, **kwargs) -> str`.
    Returns None on success, error message on failure.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Cannot parse code: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            args = node.args
            # Must have exactly one positional arg named 'input'
            if len(args.args) != 1 or args.args[0].arg != "input":
                return "main() must have exactly one positional arg named 'input'"
            # Must have **kwargs
            if args.kwarg is None:
                return "main() must accept **kwargs"
            # Must have -> str return annotation
            if not (isinstance(node.returns, ast.Name) and node.returns.id == "str"):
                return "main() must have -> str return annotation"
            return None

    return "No function named 'main' found"


def check_code_node(code: str) -> str | None:
    """Validate code against the CodeNode pydantic schema.
    Returns None on success, error message on failure.
    """
    from pydantic import ValidationError

    from apps.pipelines.nodes.nodes import CodeNode

    try:
        CodeNode.model_validate({"code": code, "name": "code", "node_id": "code", "django_node": None})
        return None
    except ValidationError as e:
        return f"CodeNode validation failed: {e}"


def check_count(messages: list[str], expected: int) -> str | None:
    """Check that the message list has the expected count.
    Returns None on success, error message on failure.
    """
    actual = len(messages)
    if actual != expected:
        return f"Expected {expected} messages, got {actual}"
    return None


def check_max_words(messages: list[str], limit: int) -> str | None:
    """Check that every message has at most `limit` words.
    Returns None on success, error message on failure.
    """
    violations = []
    for i, msg in enumerate(messages):
        word_count = len(msg.split())
        if word_count > limit:
            violations.append(f"Message {i} has {word_count} words (limit {limit}): {msg!r}")
    if violations:
        return "\n".join(violations)
    return None
```

**Step 5: Run tests to verify they pass**

Run: `pytest apps/help/evals/test_checks.py -v`
Expected: All tests PASS.

**Step 6: Lint**

Run: `ruff check apps/help/evals/ --fix && ruff format apps/help/evals/`

**Step 7: Commit**

```bash
git add apps/help/evals/
git commit -m "feat: add programmatic check functions for help agent evals"
```

---

### Task 3: Create conftest with fixture loader, LLM judge, and run_checks dispatcher

**Files:**
- Create: `apps/help/evals/conftest.py`
- Create: `apps/help/evals/test_conftest.py`

**Step 1: Write failing tests for conftest utilities**

Create `apps/help/evals/test_conftest.py`:

```python
from pathlib import Path
from unittest import mock

import pytest

from apps.help.evals.conftest import JudgeResult, load_fixtures, llm_judge, run_checks


class TestLoadFixtures:
    def test_loads_yaml_file(self, tmp_path):
        fixture_file = tmp_path / "test.yml"
        fixture_file.write_text(
            "- id: case1\n  input:\n    query: hello\n  checks:\n    - type: syntax\n"
        )
        cases = load_fixtures(fixture_file)
        assert len(cases) == 1
        assert cases[0]["id"] == "case1"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_fixtures(Path("/nonexistent/file.yml"))


class TestLlmJudge:
    @mock.patch("apps.help.evals.conftest.build_system_agent")
    def test_returns_pass_and_reason(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": JudgeResult(passed=True, reason="Looks good")
        }
        mock_build.return_value = mock_agent

        passed, reason = llm_judge("some output", "must be good")
        assert passed is True
        assert reason == "Looks good"

    @mock.patch("apps.help.evals.conftest.build_system_agent")
    def test_returns_fail(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {
            "structured_response": JudgeResult(passed=False, reason="Not good enough")
        }
        mock_build.return_value = mock_agent

        passed, reason = llm_judge("bad output", "must be good")
        assert passed is False


class TestRunChecks:
    def test_passing_checks(self):
        code = "def main(input: str, **kwargs) -> str:\n    return input"
        checks = [{"type": "syntax"}, {"type": "has_main"}]
        # Should not raise
        run_checks(code, checks, output_field="code")

    def test_failing_check_raises(self):
        code = "def foo():\n    pass"
        checks = [{"type": "syntax"}, {"type": "has_main"}]
        with pytest.raises(AssertionError, match="has_main"):
            run_checks(code, checks, output_field="code")
```

**Step 2: Run tests to verify they fail**

Run: `pytest apps/help/evals/test_conftest.py -v`
Expected: ImportError — `conftest` functions don't exist yet.

**Step 3: Implement conftest.py**

Create `apps/help/evals/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.conf import settings
from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.evals.checks import (
    check_code_node,
    check_count,
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
    response = agent.invoke(
        {"messages": [{"role": "user", "content": f"Criteria: {criteria}\n\nOutput:\n{output}"}]}
    )
    result: JudgeResult = response["structured_response"]
    return result.passed, result.reason


# Map check types to functions. Each returns None on success, error string on failure.
CHECK_DISPATCH: dict[str, callable] = {
    "syntax": lambda output, _params: check_syntax(output),
    "has_main": lambda output, _params: check_has_main(output),
    "code_node": lambda output, _params: check_code_node(output),
    "count": lambda output, params: check_count(output, params["expected"]),
    "max_words": lambda output, params: check_max_words(output, params["per_message"]),
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
    """Check if system agent API keys are configured."""
    return bool(getattr(settings, "SYSTEM_AGENT_MODELS_HIGH", None)) or bool(
        getattr(settings, "SYSTEM_AGENT_MODELS_LOW", None)
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip eval tests if system agent models are not configured."""
    skip_eval = pytest.mark.skip(reason="System agent models not configured (need API keys)")
    for item in items:
        if "eval" in item.keywords and not _system_agent_models_configured():
            item.add_marker(skip_eval)
```

**Step 4: Run tests to verify they pass**

Run: `pytest apps/help/evals/test_conftest.py -v`
Expected: All tests PASS.

**Step 5: Lint**

Run: `ruff check apps/help/evals/ --fix && ruff format apps/help/evals/`

**Step 6: Commit**

```bash
git add apps/help/evals/
git commit -m "feat: add conftest with fixture loader, LLM judge, and run_checks"
```

---

### Task 4: Create YAML fixtures for both agents

**Files:**
- Create: `apps/help/evals/fixtures/code_generate.yml`
- Create: `apps/help/evals/fixtures/progress_messages.yml`

**Step 1: Create code_generate fixtures**

Create `apps/help/evals/fixtures/code_generate.yml`:

```yaml
- id: basic_hello_world
  input:
    query: "Write a function that returns 'Hello, World!'"
    context: ""
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The generated code defines a main function that returns the string 'Hello, World!' when called"

- id: echo_input
  input:
    query: "Write a function that echoes back the user's input unchanged"
    context: ""
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The generated code returns the input parameter unchanged"

- id: fix_syntax_error
  input:
    query: "Fix this code"
    context: "def main(input: str, **kwargs) -> str:\n    return input.upper("
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The code fixes the syntax error (unclosed parenthesis) and preserves the .upper() behavior"

- id: use_participant_data
  input:
    query: "Write a function that reads the participant's name from participant data and greets them"
    context: ""
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The code calls get_participant_data() to read data and returns a greeting string that includes the participant's name"

- id: modify_existing_code
  input:
    query: "Add error handling so it returns 'Error' if the input is empty"
    context: "def main(input: str, **kwargs) -> str:\n    return input.upper()"
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The code checks if input is empty and returns 'Error' in that case, otherwise returns the uppercased input"
```

**Step 2: Create progress_messages fixtures**

Create `apps/help/evals/fixtures/progress_messages.yml`:

```yaml
- id: generic_chatbot
  input:
    chatbot_name: "HealthBot"
    chatbot_description: "A health and wellness advice chatbot"
  checks:
    - type: count
      expected: 30
    - type: max_words
      per_message: 6
    - type: llm_judge
      criteria: >
        All messages are short (2-4 words), encouraging, and appropriate for a health chatbot.
        Messages should not contain technical jargon, apologies, or promises about results.
        Messages should suggest active processing (e.g. thinking, analyzing).

- id: no_description
  input:
    chatbot_name: "QuickBot"
  checks:
    - type: count
      expected: 30
    - type: max_words
      per_message: 6
    - type: llm_judge
      criteria: >
        All messages are short, encouraging progress updates.
        Messages should not reference any specific domain since no description was given.

- id: customer_support_bot
  input:
    chatbot_name: "SupportPro"
    chatbot_description: "Handles customer complaints and returns for an e-commerce store"
  checks:
    - type: count
      expected: 30
    - type: max_words
      per_message: 6
    - type: llm_judge
      criteria: >
        Messages are short, friendly progress indicators appropriate for customer support.
        No negative tone. No technical jargon. Should feel reassuring.
```

**Step 3: Commit**

```bash
git add apps/help/evals/fixtures/
git commit -m "feat: add YAML eval fixtures for code_generate and progress_messages agents"
```

---

### Task 5: Create eval test files for both agents

**Files:**
- Create: `apps/help/evals/test_code_generate_eval.py`
- Create: `apps/help/evals/test_progress_messages_eval.py`

**Step 1: Create CodeGenerateAgent eval tests**

Create `apps/help/evals/test_code_generate_eval.py`:

```python
import pytest

from apps.help.agents.code_generate import CodeGenerateAgent, CodeGenerateInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "code_generate.yml")


@pytest.mark.eval
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_code_generate(case):
    agent = CodeGenerateAgent(input=CodeGenerateInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"], output_field="code")
```

**Step 2: Create ProgressMessagesAgent eval tests**

Create `apps/help/evals/test_progress_messages_eval.py`:

```python
import pytest

from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "progress_messages.yml")


@pytest.mark.eval
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_progress_messages(case):
    agent = ProgressMessagesAgent(input=ProgressMessagesInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"], output_field="messages")
```

**Step 3: Verify tests are collected but skipped (no API keys in test env)**

Run: `pytest apps/help/evals/ -m eval -v --co`
Expected: Tests collected. If no API keys configured, they'll be marked as skip.

**Step 4: Verify existing tests still work (evals excluded)**

Run: `pytest apps/help/tests.py -v`
Expected: All existing unit tests pass. No eval tests collected.

**Step 5: Lint**

Run: `ruff check apps/help/evals/ --fix && ruff format apps/help/evals/`

**Step 6: Commit**

```bash
git add apps/help/evals/
git commit -m "feat: add eval test files for code_generate and progress_messages agents"
```

---

### Task 6: Final verification and cleanup

**Step 1: Run full unit test suite to confirm no regressions**

Run: `pytest apps/help/ -v`
Expected: All existing unit tests pass. Eval tests are NOT collected (excluded by default marker filter).

**Step 2: Dry-run eval collection**

Run: `pytest apps/help/evals/ -m eval --co -v`
Expected: 8 eval test cases collected (5 code_generate + 3 progress_messages).

**Step 3: Lint all new files**

Run: `ruff check apps/help/evals/ --fix && ruff format apps/help/evals/`

**Step 4: Final commit if any lint changes**

```bash
git add -u
git commit -m "chore: lint cleanup for help agent evals"
```
