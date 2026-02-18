# Static Evals for Help Agents

## Goal

Add comprehensive static evaluations for the agents in `apps/help` (CodeGenerateAgent, ProgressMessagesAgent) that test:
- **Structural correctness** — outputs parse, match schemas, satisfy hard constraints
- **Output quality** — LLM-as-judge assesses subjective quality criteria
- **Prompt regression** — fixed test cases detect when prompt changes degrade output

## Approach

Lightweight pytest-native: YAML fixtures define test cases, a custom `@pytest.mark.eval` marker separates evals from unit tests, and a thin `llm_judge()` helper handles quality assessment. Real LLM calls (not mocked).

## File Layout

```
apps/help/
  evals/
    __init__.py
    conftest.py                    # fixtures, markers, llm_judge helper, skip logic
    checks.py                      # programmatic check functions
    fixtures/
      code_generate.yml            # test cases for CodeGenerateAgent
      progress_messages.yml        # test cases for ProgressMessagesAgent
    test_code_generate_eval.py     # eval tests
    test_progress_messages_eval.py # eval tests
```

## YAML Fixture Format

### CodeGenerateAgent (`code_generate.yml`)

```yaml
- id: basic_hello_world
  input:
    query: "Write a function that returns 'Hello, World!'"
    context: ""
  checks:
    # Programmatic checks
    - type: syntax          # code parses as valid Python via ast.parse()
    - type: has_main        # defines def main(input: str, **kwargs) -> str
    - type: code_node       # validates against CodeNode schema
    # LLM judge checks
    - type: llm_judge
      criteria: "The code returns the string 'Hello, World!' when called"

- id: fix_broken_code
  input:
    query: "Fix this code"
    context: "def main(input: str, **kwargs) -> str:\n    return input.upper("
  checks:
    - type: syntax
    - type: has_main
    - type: code_node
    - type: llm_judge
      criteria: "The code fixes the syntax error and preserves the .upper() intent"
```

### ProgressMessagesAgent (`progress_messages.yml`)

```yaml
- id: generic_chatbot
  input:
    chatbot_name: "HealthBot"
    chatbot_description: "A health advice chatbot"
  checks:
    - type: count
      expected: 30
    - type: max_words
      per_message: 6
    - type: llm_judge
      criteria: >
        Messages are short, encouraging, and appropriate for a health chatbot.
        No technical jargon. No apologies. No promises about results.
```

## pytest Configuration

Add `eval` marker to `pyproject.toml`, excluded from default test runs:

```toml
[tool.pytest.ini_options]
addopts = "--ds=config.settings --reuse-db --strict-markers --tb=short -m \"not integration and not eval\""
markers = [
    "integration: marks tests as integration tests (deselected by default)",
    "eval: marks tests as LLM evaluation tests (deselected by default, requires API keys)",
]
```

Run evals explicitly:

```bash
pytest apps/help/evals/ -m eval -v
pytest apps/help/evals/test_code_generate_eval.py -m eval -k "basic_hello_world" -v
```

## Components

### conftest.py

- **`load_fixtures(filename)`** — reads YAML file, returns list of case dicts for parametrize
- **`llm_judge(output, criteria)`** — calls LLM to assess output against criteria, returns `(passed: bool, reason: str)`
- **Skip logic** — auto-skip all eval tests if `SYSTEM_AGENT_MODELS_HIGH` / `SYSTEM_AGENT_MODELS_LOW` settings are empty

### checks.py — Programmatic Checks

| Check | Agent | Logic |
|-------|-------|-------|
| `check_syntax(code)` | CodeGenerate | `ast.parse()` succeeds |
| `check_has_main(code)` | CodeGenerate | AST contains `def main(input: str, **kwargs) -> str` |
| `check_code_node(code)` | CodeGenerate | `CodeNode.model_validate(...)` passes |
| `check_count(messages, expected)` | ProgressMessages | `len(messages) == expected` |
| `check_max_words(messages, limit)` | ProgressMessages | all messages within word limit |

### run_checks(result, checks)

Dispatcher function that iterates over the `checks` list, calls the appropriate check function (programmatic or `llm_judge`), and collects all failures into a single assertion message.

### LLM Judge

```python
def llm_judge(output: str, criteria: str) -> tuple[bool, str]:
    agent = build_system_agent(
        "low",
        "You are an evaluation judge. Assess whether the given output meets the criteria. "
        "Respond with a JSON object: {\"pass\": true/false, \"reason\": \"brief explanation\"}",
        response_format=JudgeResult,
    )
    response = agent.invoke({
        "messages": [{"role": "user", "content": f"Criteria: {criteria}\n\nOutput:\n{output}"}]
    })
    result = response["structured_response"]
    return result.passed, result.reason
```

Uses "low" tier model to keep costs down. Reuses existing `build_system_agent` infrastructure.

### Test Pattern

```python
@pytest.mark.eval
@pytest.mark.parametrize("case", load_fixtures("code_generate.yml"), ids=lambda c: c["id"])
def test_code_generate(case):
    agent = CodeGenerateAgent(input=CodeGenerateInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"])
```

## Key Decisions

- **Real LLM calls** — not mocked, catches real regressions from model or prompt changes
- **Excluded from default pytest** — only run explicitly via `-m eval`
- **YAML fixtures** — easy to add new test cases without writing Python
- **Low-tier judge** — quality assessment doesn't need expensive models
- **No scoring/trending** — pass/fail for now; can add scoring later if needed
