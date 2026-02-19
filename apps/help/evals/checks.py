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
