# Help Agent Evals

The `apps/help/evals/` directory contains evaluation tests for the AI agents in `apps/help/agents/`. These tests verify that agents produce correct, well-formed output on representative inputs using a mix of deterministic checks and LLM-based judging.

## Overview

Eval tests are regular pytest tests marked with `@pytest.mark.eval`. They are automatically skipped if the required API keys are not configured, so they do not break CI for contributors without LLM access.

```
apps/help/evals/
├── conftest.py              # Shared fixtures, check dispatch, LLM judge
├── checks.py                # Deterministic check functions
├── test_code_generate_eval.py
├── test_filter_eval.py
├── test_progress_messages_eval.py
├── test_checks.py           # Unit tests for check functions (no LLM)
└── fixtures/
    ├── code_generate.yml
    ├── filter.yml
    └── progress_messages.yml
```

## Running Evals

Evals require `SYSTEM_AGENT_MODELS_HIGH` and `SYSTEM_AGENT_MODELS_LOW` to be set (both tiers are used: agents generate output on `HIGH`, the LLM judge evaluates on `LOW`).

```bash
# Run all evals
uv run pytest apps/help/evals/ -m eval -v

# Run a specific eval file
uv run pytest apps/help/evals/test_filter_eval.py -m eval -v

# Run a specific case by ID
uv run pytest apps/help/evals/test_code_generate_eval.py -m eval -k basic_hello_world -v

# Run deterministic check unit tests (no LLM required)
uv run pytest apps/help/evals/test_checks.py -v
```

## Fixture Format

Each agent has a YAML fixture file in `fixtures/` containing a list of test cases:

```yaml
- id: unique_case_id          # used as pytest param ID
  input:                       # kwargs passed to the agent's Input model
    query: "some request"
    context: ""
  checks:                      # list of checks run against the agent output
    - type: syntax
    - type: has_main
    - type: execute
      input: "test"
      expected: "TEST"
```

The `input` keys map directly to the agent's Pydantic input model. All checks are run and failures are collected before raising, so you see all failures at once.

## Check Types

Checks are defined in `checks.py` and dispatched in `conftest.py`. Each check returns `None` on success or an error string on failure.

### Deterministic checks

| Check | Description | Extra params |
|---|---|---|
| `syntax` | Valid Python (ast.parse) | — |
| `has_main` | Defines `def main(input: str, **kwargs) -> str:` | — |
| `code_node` | Passes `CodeNode` Pydantic validation | — |
| `execute` | Runs code in sandbox, checks output | `input`, `expected` |
| `count` | List has expected length | `expected` |
| `max_words` | Every list item is under word limit | `per_message` |
| `filter_params` | Filter columns match expected set | `expected` (list of column names) |
| `exact_filters` | Filters match exactly (column, operator, value) | `expected` (list of `{column, operator, value}`) |

### LLM judge

Use `llm_judge` for outputs that are correct-by-degree rather than by exact match:

```yaml
- type: llm_judge
  criteria: >
    The code calls get_participant_data() to read data and
    returns a greeting string that includes the participant's name.
```

The judge is strict: it only passes if the output clearly meets the criteria. Write criteria as objective, observable properties of the output.

## Design Notes

- **Auto-skip**: `pytest_collection_modifyitems` in `conftest.py` skips all `eval`-marked tests when API keys are absent. No `pytest.ini` changes needed.
- **LLM judge tier**: The judge always uses the `LOW` model tier to keep evaluation costs down.
- **Retry logic**: `CodeGenerateAgent` retries up to 3 times if `CodeNode` validation fails, so eval tests exercise the full retry loop.
- **Inline tests**: `test_filter_eval.py` also contains non-parametrized tests (e.g. `test_filter_experiment_uses_option_ids`) that set up specific DB state to verify agent tool-use behavior. These can co-exist with fixture-driven cases in the same file.
