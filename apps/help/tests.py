from apps.help.utils import extract_function_signature, get_python_node_coder_prompt


def test_get_python_node_coder_prompt():
    current_code = "bla bla bla"
    error = "alb alb alb"
    prompt = get_python_node_coder_prompt(current_code, error)
    assert "get_participant_data" in prompt
    assert current_code in prompt
    assert error in prompt


class TestExtractFunctionSignature:
    def test_function_with_args(self):
        def func_with_args(a, b, c=10):
            """Function with arguments."""
            pass

        result = extract_function_signature("func_with_args", func_with_args)
        expected = 'def func_with_args(a, b, c=10):\n    """Function with arguments."""\n'
        assert result == expected

    def test_function_without_docstring(self):
        def no_docstring_func(x):
            return x

        result = extract_function_signature("no_docstring_func", no_docstring_func)
        expected = "def no_docstring_func(x):\n    pass\n"
        assert result == expected

    def test_function_with_multiline_docstring(self):
        def multiline_func():
            """This is a function with a multiline docstring.

            It has multiple lines.
            And provides detailed information."""
            pass

        result = extract_function_signature("multiline_func", multiline_func)
        expected = '''def multiline_func():
    """This is a function with a multiline docstring.

    It has multiple lines.
    And provides detailed information."""
'''
        assert result == expected

    def test_non_callable_object_returns_none(self):
        result = extract_function_signature("not_callable", "string")
        assert result is None
