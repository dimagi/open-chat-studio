import ast
import math

from RestrictedPython import RestrictingNodeTransformer, compile_restricted

EMPTY_EXPRESSION_ERROR = "Error: empty expression"

EXPRESSION_TOO_LARGE_ERROR = "Error: expression too large"
MAX_DIGIT_ERROR = "Error: result exceeds the maximum digit size"
GENERIC_PARSE_ERROR = "Unable to parse the expression. Please check the syntax."

ALLOWED_OPERATORS = {
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
}

UNICODE_REPLACEMENTS = {"＋": "+", "^": "**", "×": "*", "÷": "/", "−": "-"}


class RestrictedOperationsTransformer(RestrictingNodeTransformer):
    def visit_BinOp(self, node):
        if node.op.__class__ not in ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported operation: {node.op}")
        return super().visit_BinOp(node)

    def visit_Lambda(self, node):
        raise ValueError("Unsupported expression")


def calculate(expression: str):
    expression = expression.strip()
    for old, new in UNICODE_REPLACEMENTS.items():
        expression = expression.replace(old, new)

    if not expression:
        return EMPTY_EXPRESSION_ERROR
    if len(expression) > 200:
        return EXPRESSION_TOO_LARGE_ERROR

    filename = "<inline_code>"
    try:
        byte_code = compile_restricted(
            expression, filename=filename, mode="eval", policy=RestrictedOperationsTransformer
        )
    except SyntaxError as e:
        message = GENERIC_PARSE_ERROR
        if isinstance(getattr(e, "msg", None), tuple) and "SyntaxError" in e.msg[0]:
            try:
                message = e.msg[0].split("SyntaxError:")[-1].strip()
            except Exception:
                message = str(e)
        return f"Error: {message}"
    except RecursionError:
        return EXPRESSION_TOO_LARGE_ERROR
    except ValueError as e:
        return f"Error: {e}"

    allowed_names = {k: getattr(math, k) for k in dir(math) if not k.startswith("__")}
    allowed_names.update(
        {
            "pi": math.pi,
            "e": math.e,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
        }
    )
    try:
        result = eval(byte_code, {"__builtins__": {}}, allowed_names)
    except Exception as e:
        return f"Error: {e}"

    try:
        return str(result)
    except ValueError:
        return MAX_DIGIT_ERROR
