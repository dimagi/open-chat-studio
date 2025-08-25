import textwrap

import pytest
from pydantic import ValidationError

from apps.evaluations.evaluators import PythonEvaluator
from apps.evaluations.exceptions import EvaluationRunException
from apps.utils.factories.evaluations import EvaluationMessageFactory


@pytest.mark.parametrize(
    ("code", "message_input", "expected_output"),
    [
        (
            textwrap.dedent("""
            def main(input, output, context, full_history, generated_response, **kwargs):
                return {"out": f'Hello, {input.content}!'}
            """),
            {"content": "World", "role": "human"},
            {"out": "Hello, World!"},
        ),
        (
            textwrap.dedent("""
            def main(input, output, context, full_history, generated_response, **kwargs):
                return {'result': 'foo'}"
            """),
            {"content": "World", "role": "ai"},
            {"result": "foo"},
        ),
    ],
)
@pytest.mark.django_db()
def test_python_evaluator(code, message_input, expected_output):
    evaluator = PythonEvaluator(code=code)
    message = EvaluationMessageFactory(input=message_input)
    evaluator_output = evaluator.run(message, "")
    assert evaluator_output.result == expected_output


@pytest.mark.django_db()
def test_python_evaluator_traceback():
    code_set = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        # this is a comment
        a = 1
        b = 2
        if a != b:
           fail("error message")
        return {"result": input}
    """)

    evaluator = PythonEvaluator(code=code_set)
    message = EvaluationMessageFactory()

    with pytest.raises(EvaluationRunException) as exc_info:  # EvaluationRunException wraps the actual error
        evaluator.run(message, "")

    error_msg = str(exc_info.value)
    assert "NameError" in error_msg
    assert "name 'fail' is not defined" in error_msg
    assert "Context:" in error_msg
    assert "7:" in error_msg  # Line number should be in traceback
    assert 'fail("error message")' in error_msg


EXTRA_FUNCTION = """
def other(foo):
    return f"other {foo}"

def main(input, output, context, full_history, generated_response, **kwargs):
    return {"result": other(input)}
"""


@pytest.mark.parametrize(
    ("code", "expected_error"),
    [
        ("this{}", "SyntaxError: invalid syntax at statement: 'this{}"),
        (
            EXTRA_FUNCTION,
            (
                "You can only define a single function, 'main' at the top level. "
                "You may use nested functions inside that function if required"
            ),
        ),
        ("def other(input):\n\treturn input", "You must define a 'main' function"),
        (
            "def main(input, others, **kwargs):\n\treturn input",
            "The main function should have the signature "
            r"main\(input, output, context, full_history, generated_response, \*\*kwargs\) only",
        ),
    ],
)
def test_python_evaluator_build_errors(code, expected_error):
    with pytest.raises(ValidationError, match=expected_error):
        PythonEvaluator(code=code)


@pytest.mark.parametrize(
    ("code", "expected_error"),
    [
        (
            "import collections\ndef main(input, output, context, full_history, generated_response, **kwargs):"
            "\n\treturn {'result': input}",
            "Importing 'collections' is not allowed",
        ),
        (
            "def main(input, output, context, full_history, generated_response, **kwargs):"
            "\n\treturn {'result': f'Hello, {blah}!'}",
            "name 'blah' is not defined",
        ),
        (
            "def main(input, output, context, full_history, generated_response, **kwargs):\n\treturn 'foo'",
            "The python function did not return a dictionary",
        ),
    ],
)
@pytest.mark.django_db()
def test_python_evaluator_runtime_errors(code, expected_error):
    evaluator = PythonEvaluator(code=code)
    message = EvaluationMessageFactory()

    with pytest.raises(EvaluationRunException, match=expected_error):
        evaluator.run(message, "")
