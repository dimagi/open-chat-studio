from apps.help.evals.checks import (
    check_code_node,
    check_count,
    check_execute,
    check_filter_params,
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


class TestCheckExecute:
    def test_correct_output(self):
        code = 'def main(input: str, **kwargs) -> str:\n    return "Hello, World!"'
        assert check_execute(code, "anything", "Hello, World!") is None

    def test_echo_input(self):
        code = "def main(input: str, **kwargs) -> str:\n    return input"
        assert check_execute(code, "test value", "test value") is None

    def test_wrong_output(self):
        code = 'def main(input: str, **kwargs) -> str:\n    return "wrong"'
        result = check_execute(code, "anything", "expected")
        assert result is not None
        assert "Expected" in result
        assert "'expected'" in result

    def test_runtime_error(self):
        code = "def main(input: str, **kwargs) -> str:\n    return 1 / 0"
        result = check_execute(code, "anything", "whatever")
        assert result is not None
        assert "Execution failed" in result


class TestCheckCount:
    def test_correct_count(self):
        assert check_count(["a", "b", "c"], 3) is None

    def test_wrong_count(self):
        result = check_count(["a", "b"], 3)
        assert result is not None
        assert "2" in result
        assert "3" in result


class TestCheckMaxWords:
    def test_within_limit(self):
        assert check_max_words(["two words", "three little words"], 4) is None

    def test_exceeds_limit(self):
        result = check_max_words(["this has way too many words in it"], 4)
        assert result is not None


class TestCheckFilterParams:
    def _make_filter(self, column):
        from apps.web.dynamic_filters.datastructures import ColumnFilterData

        return ColumnFilterData(column=column, operator="any of", value="x")

    def test_exact_match(self):
        filters = [self._make_filter("state"), self._make_filter("channels")]
        assert check_filter_params(filters, ["channels", "state"]) is None

    def test_missing_param(self):
        filters = [self._make_filter("state")]
        result = check_filter_params(filters, ["state", "channels"])
        assert result is not None
        assert "channels" in result

    def test_extra_param(self):
        filters = [self._make_filter("state"), self._make_filter("tags")]
        result = check_filter_params(filters, ["state"])
        assert result is not None
        assert "tags" in result

    def test_empty(self):
        assert check_filter_params([], []) is None
