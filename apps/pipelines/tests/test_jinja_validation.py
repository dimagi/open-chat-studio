from collections import OrderedDict

from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SecurityError

from apps.pipelines.nodes.nodes import format_jinja_error


class TestFormatJinjaError:
    def test_undefined_error_with_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "subject", context={"input": "", "temp_state": {}})
        assert 'UndefinedError in field "subject"' in result
        assert "Available variables: input, temp_state" in result

    def test_undefined_error_without_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body")
        assert 'UndefinedError in field "body"' in result
        assert "Available variables" not in result

    def test_syntax_error(self):
        exc = TemplateSyntaxError("unexpected '}'", lineno=3)
        result = format_jinja_error(exc, "template_string")
        assert 'TemplateSyntaxError in field "template_string"' in result
        assert "(line 3)" in result

    def test_syntax_error_no_lineno(self):
        exc = TemplateSyntaxError("unexpected end of template", lineno=None)
        result = format_jinja_error(exc, "body")
        assert 'TemplateSyntaxError in field "body"' in result
        assert "(line" not in result

    def test_security_error(self):
        exc = SecurityError("access to attribute 'mro' of 'type' object is unsafe")
        result = format_jinja_error(exc, "body")
        assert 'SecurityError in field "body"' in result

    def test_generic_exception(self):
        exc = ValueError("something broke")
        result = format_jinja_error(exc, "body")
        assert 'Jinja2 error in field "body"' in result
        assert "ValueError" in result

    def test_context_keys_preserve_insertion_order(self):
        """Available variables should appear in insertion order, not sorted."""

        ctx = OrderedDict([("zebra", 1), ("alpha", 2), ("middle", 3)])
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body", context=ctx)
        assert "Available variables: zebra, alpha, middle" in result
