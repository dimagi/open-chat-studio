import pytest

from apps.chat.agent.calculator import (
    EMPTY_EXPRESSION_ERROR,
    EXPRESSION_TOO_LARGE_ERROR,
    GENERIC_PARSE_ERROR,
    MAX_DIGIT_ERROR,
    calculate,
)


@pytest.mark.parametrize(
    ("expression", "result"),
    [
        ("2*2", "4"),
        ("2×2", "4"),
        ("2 * 3 + 4", "10"),
        ("sin(pi/2)", "1.0"),
        ("sqrt(16)", "4.0"),
        ("2**3", "8"),
        ("2^3", "8"),
        ("6/2", "3.0"),
        ("6÷2", "3.0"),
        ("-5 + 3", "-2"),
        ("1.5 + 2.7", "4.2"),
        ("0 * 100", "0"),
        ("(2 + 3) * 4", "20"),
        ("10 % 3", "1"),
        ("17 // 5", "3"),
        ("1e3 + 2e2", "1200.0"),
        ("sin(30)", "-0.9880316240928618"),
        ("'hello' + 'world'", "helloworld"),
        ("(" * 10 + "1" + ")" * 10, "1"),  # deep nesting
        # Error cases
        ("5/0", "Error: division by zero"),
        ("10 / (5 - 5)", "Error: division by zero"),
        ("(2 + 3", "Error: '(' was never closed at statement: '(2 + 3'"),
        ("3 + a", "Error: name 'a' is not defined"),
        ("   ", EMPTY_EXPRESSION_ERROR),
        ("2..3 + 1", "Error: invalid syntax at statement: '2..3 + 1'"),
        ("10**10000 * 10**10000", MAX_DIGIT_ERROR),
        ("__import__('os').system('ls')", f"Error: {GENERIC_PARSE_ERROR}"),
        ("exec('print(\"hacked\")')", f"Error: {GENERIC_PARSE_ERROR}"),
        ("eval('2+2')", f"Error: {GENERIC_PARSE_ERROR}"),
        (" + ".join(["1"] * 1000), EXPRESSION_TOO_LARGE_ERROR),  # large expression
    ],
)
def test_calculator(expression, result):
    assert calculate(expression) == result
