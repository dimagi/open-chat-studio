import html
import re

import pytest

from apps.ocs_notifications.formatting import markdown_literal, message_html, message_mrkdwn, message_text


def _visible(rendered: str) -> str:
    """The text a reader sees in rendered message HTML."""
    without_breaks = re.sub(r"<br\s*/?>", "", rendered)
    return html.unescape(re.sub(r"<[^>]+>", "", without_breaks)).strip()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("KeyError: '__init__'", id="dunder"),
        pytest.param("Weather *beta*", id="asterisks"),
        pytest.param("<Response [500]>", id="html-like"),
        pytest.param("a & b < c", id="ampersand"),
        pytest.param("[docs](https://example.com)", id="link-syntax"),
        pytest.param("`code`", id="backticks"),
        pytest.param("C:\\temp\\file", id="backslashes"),
        pytest.param("snake_case_name", id="intraword-underscores"),
        pytest.param("first\n# not a heading\n1. not a list\n- not a bullet", id="line-start-markers"),
    ],
)
def test_an_interpolated_value_renders_exactly_as_written(value):
    assert _visible(message_html(f"Failed: {markdown_literal(value)}")) == f"Failed: {value}"


def test_a_plain_message_renders_unchanged():
    message = "The model 'gpt-4.1-nano' has been deprecated and will be removed soon."
    assert message_html(message) == f"<p>{message}</p>"


def test_authored_markdown_formats():
    rendered = message_html("**Update** the [embed snippet](https://example.com/docs)")
    assert "<strong>Update</strong>" in rendered
    assert re.search(r'<a href="https://example.com/docs"[^>]*>embed snippet</a>', rendered)


def test_html_in_a_message_is_shown_not_run():
    rendered = message_html("<script>alert(1)</script>")
    assert "<script" not in rendered
    assert _visible(rendered) == "<script>alert(1)</script>"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("**Update** now", "*Update* now", id="bold"),
        pytest.param("*soon*", "_soon_", id="italic"),
        pytest.param("Run `inv dev`", "Run `inv dev`", id="inline-code"),
        pytest.param(
            "See [docs](https://example.com/a?b=1&c=2)", "See <https://example.com/a?b=1&amp;c=2|docs>", id="link"
        ),
        pytest.param("- one\n- two", "• one\n• two", id="bullets"),
        pytest.param("1. one\n2. two", "1. one\n2. two", id="numbered"),
        pytest.param("# Title\nbody", "*Title*\n\nbody", id="heading"),
        pytest.param("line one\nline two", "line one\nline two", id="line-break"),
        pytest.param("a < b & c", "a &lt; b &amp; c", id="slack-reserved-characters"),
        pytest.param(f"Failed: {markdown_literal('KeyError: __init__')}", "Failed: KeyError: __init__", id="literal"),
    ],
)
def test_message_mrkdwn(message, expected):
    assert message_mrkdwn(message) == expected


def test_message_text_drops_markdown_escapes():
    value = "KeyError: '__init__' in *beta*"
    assert message_text(f"Failed: {markdown_literal(value)}") == f"Failed: {value}"
