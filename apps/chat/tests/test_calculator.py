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
        ("0x10 + 8", "24"),  # hex
        ("1e-1000 + 1", "1.0"),  # very small number
        ("-0.0 + 0.0", "0.0"),  # negative 0
        ("1_000 + 2_000", "3000"),
        ("(" + "1+" * 50 + "1)*2", "102"),
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
        ("2(3 + 4)", "Error: 'int' object is not callable"),
        ("'2' + 3", 'Error: can only concatenate str (not "int") to str'),
        ('"2" + 3', 'Error: can only concatenate str (not "int") to str'),
        ("2 + \\n3", "Error: unexpected character after line continuation character at statement: '2 + \\\\n3'"),
        ("\\x32 + 3", "Error: unexpected character after line continuation character at statement: '\\\\x32 + 3'"),
        ("(2).__class__", f"Error: {GENERIC_PARSE_ERROR}"),
        ("__builtins__['eval']('2+2')", f"Error: {GENERIC_PARSE_ERROR}"),
        ("(lambda: 2+2)()", "Error: Unsupported expression"),
        ("sum([x for x in range(10)])", "Error: name '_getiter_' is not defined"),
        ("float('inf') + 1", "Error: name 'float' is not defined"),
        ("2,5 + 3,7", "(2, 8, 7)"),  # European decimal
    ],
)
def test_calculator(expression, result):
    assert calculate(expression) == result


def test_large_expressions():
    # keep these out of the parameterized test to avoid large outputs in the test logs
    assert calculate(" + ".join(["1"] * 1000)) == EXPRESSION_TOO_LARGE_ERROR
    assert calculate(f"{'9' * 100000} + 1") == EXPRESSION_TOO_LARGE_ERROR
