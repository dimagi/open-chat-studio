import json
import re

import pytest
from django.template.loader import render_to_string


class _Message:
    def __init__(self, message, tags="info"):
        self.message = message
        self.tags = tags


NOTIFY_RE = re.compile(r'alertify\.notify\("(?P<message>.*?)", "(?P<tags>.*?)", 100\);')


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("Line one.\nLine two.", id="newline"),
        pytest.param('He said "hi"', id="double-quote"),
        pytest.param("</script><script>alert(1)</script>", id="script-tag"),
        pytest.param("back\\slash", id="backslash"),
    ],
)
def test_messages_survive_the_javascript_string_literal(message):
    """Messages are interpolated into a JS string literal, so anything unescaped breaks the toast."""
    rendered = render_to_string("web/components/messages.html", {"messages": [_Message(message)]})

    literal = NOTIFY_RE.search(rendered).group("message")
    # A JS string literal that parses as JSON is a literal the browser can parse too.
    assert json.loads(f'"{literal}"') == message
