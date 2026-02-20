from __future__ import annotations

import ast
import json


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


def check_execute(code: str, input_value: str, expected: str) -> str | None:
    """Execute the code in the RestrictedPython sandbox and check the output.
    Returns None on success, error message on failure.
    """
    from apps.pipelines.nodes.nodes import CodeNode

    try:
        node = CodeNode.model_validate({"code": code, "name": "eval", "node_id": "eval", "django_node": None})
    except Exception as e:
        return f"CodeNode validation failed: {e}"

    try:
        result = node.compile_and_execute_code(input=input_value)
    except Exception as e:
        return f"Execution failed: {e}"

    if result != expected:
        return f"Expected {expected!r}, got {result!r}"
    return None


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


def check_filter_params(filters: list, expected_params: list[str]) -> str | None:
    """Check that the output contains exactly the expected column values.
    Returns None on success, error message on failure.
    """
    actual_params = sorted(f.column for f in filters)
    expected_sorted = sorted(expected_params)
    if actual_params != expected_sorted:
        return f"Expected params {expected_sorted}, got {actual_params}"
    return None


def _compare_filter_values(actual_str: str, expected) -> str | None:
    """Compare an actual filter value string against an expected value.

    If expected is a list, parse actual as JSON and compare sorted lists.
    This validates that the LLM produced valid JSON-encoded values.
    Otherwise compare as strings.
    """
    if isinstance(expected, list):
        try:
            parsed = json.loads(actual_str)
        except (json.JSONDecodeError, TypeError):
            return f"expected JSON list {expected}, got unparseable value: {actual_str!r}"
        if not isinstance(parsed, list):
            return f"expected JSON list {expected}, got non-list: {parsed!r}"
        expected_sorted = sorted(str(v) for v in expected)
        actual_sorted = sorted(str(v) for v in parsed)
        if actual_sorted != expected_sorted:
            return f"expected {expected_sorted}, got {actual_sorted}"
        return None

    expected_str = str(expected)
    if actual_str != expected_str:
        return f"expected {expected_str!r}, got {actual_str!r}"
    return None


def _format_filters(filters: list[dict]) -> str:
    lines = []
    for f in filters:
        lines.append(f"  {f['column']} {f['operator']} {f['value']!r}")
    return "\n".join(lines)


def check_exact_filters(filters: list, expected_filters: list[dict]) -> str | None:
    """Check that output filters exactly match expected filters (column, operator, value).
    Returns None on success, error message on failure.
    """
    actual = sorted(
        [{"column": f.column, "operator": f.operator, "value": f.value} for f in filters],
        key=lambda d: d["column"],
    )
    expected = sorted(expected_filters, key=lambda d: d["column"])

    if len(actual) != len(expected):
        actual_cols = [f["column"] for f in actual]
        expected_cols = [f["column"] for f in expected]
        return (
            f"Expected {len(expected)} filters {expected_cols}, got {len(actual)} {actual_cols}"
            + f"\n\nExpected:\n{_format_filters(expected)}"
            + f"\n\nActual:\n{_format_filters(actual)}"
        )

    errors = []
    for act, exp in zip(actual, expected, strict=True):
        if act["column"] != exp["column"]:
            errors.append(f"column mismatch: expected {exp['column']!r}, got {act['column']!r}")
            continue
        col = act["column"]
        if act["operator"] != exp["operator"]:
            errors.append(f"{col}: operator expected {exp['operator']!r}, got {act['operator']!r}")
        value_err = _compare_filter_values(act["value"], exp["value"])
        if value_err:
            errors.append(f"{col}: {value_err}")

    if errors:
        return (
            "Filter mismatch:\n  "
            + "\n  ".join(errors)
            + f"\n\nExpected:\n{_format_filters(expected)}"
            + f"\n\nActual:\n{_format_filters(actual)}"
        )
    return None
