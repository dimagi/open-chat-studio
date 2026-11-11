"""Notification messages are markdown: authored text may format, interpolated values are escaped."""

import html
import re
from html.parser import HTMLParser

import markdown
import nh3

_INLINE_SYNTAX = re.compile(r"([\\`*\[\]])")
# Underscores between letters or digits (``snake_case``) never emphasise, so only a run touching an edge
# needs escaping. `[^\W_]` is a letter or digit: `\w` alone counts the underscore itself.
_EDGE_UNDERSCORES = re.compile(r"(?<![^\W_])_+|_+(?![^\W_])")
_LINE_START_MARKER = re.compile(r"^(\s*)([#+-])", re.MULTILINE)
_LINE_START_NUMBER = re.compile(r"^(\s*\d+)\.", re.MULTILINE)


def markdown_literal(value) -> str:
    """``value`` escaped so a markdown message shows it exactly as written."""
    text = _INLINE_SYNTAX.sub(r"\\\1", str(value))
    text = _EDGE_UNDERSCORES.sub(lambda run: "\\_" * len(run.group()), text)
    text = _LINE_START_MARKER.sub(r"\1\\\2", text)
    return _LINE_START_NUMBER.sub(r"\1\\.", text)


def message_html(message: str) -> str:
    """Sanitised HTML for a notification message.

    HTML in the message is escaped first, so text such as ``<Response [500]>`` shows as written
    instead of being parsed as a tag and stripped. Single newlines stay line breaks, as they were
    when messages rendered as plain text.
    """
    rendered = markdown.markdown(html.escape(message, quote=False), extensions=["nl2br", "fenced_code", "tables"])
    return nh3.clean(rendered, attributes={"a": {"href", "title"}})


def message_text(message: str) -> str:
    """The message with markdown escapes removed, for plain-text fallbacks."""
    return re.sub(r"\\(.)", r"\1", message)


def message_mrkdwn(message: str) -> str:
    """The message in Slack's mrkdwn, converted from the same HTML the UI and email show."""
    converter = _MrkdwnConverter()
    converter.feed(message_html(message))
    converter.close()
    return converter.result()


class _MrkdwnConverter(HTMLParser):
    """Turns the sanitised message HTML into Slack mrkdwn."""

    _ALIASES = {"b": "strong", "i": "em", "s": "del", **dict.fromkeys(("h1", "h2", "h3", "h4", "h5", "h6"), "h")}
    _MARKERS = {"strong": "*", "em": "_", "del": "~", "code": "`"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list] = []  # [ordered, next number] per open list
        self.href: str | None = None
        self.link_text: list[str] = []
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        self._dispatch("_start_", tag, dict(attrs))

    def handle_endtag(self, tag):
        self._dispatch("_end_", tag)

    def _dispatch(self, prefix: str, tag: str, *args) -> None:
        tag = self._ALIASES.get(tag, tag)
        if tag in self._MARKERS:
            if not self.in_pre:
                self._emit(self._MARKERS[tag])
        elif handler := getattr(self, f"{prefix}{tag}", None):
            handler(*args)

    def _start_h(self, attrs):
        self._emit("*")

    def _end_h(self):
        self._emit("*\n\n")

    def _start_pre(self, attrs):
        self.in_pre = True
        self._emit("```\n")

    def _end_pre(self):
        self.in_pre = False
        self._emit("```\n\n")

    def _start_ul(self, attrs):
        self.lists.append([False, 1])

    def _start_ol(self, attrs):
        self.lists.append([True, 1])

    def _end_ul(self):
        if self.lists:
            self.lists.pop()
        if not self.lists:
            self._emit("\n\n")

    _end_ol = _end_ul

    def _start_li(self, attrs):
        ordered, number = self.lists[-1] if self.lists else (False, 1)
        indent = "    " * max(len(self.lists) - 1, 0)
        self._emit(f"\n{indent}{f'{number}.' if ordered else '•'} ")
        if self.lists and ordered:
            self.lists[-1][1] += 1

    def _start_br(self, attrs):
        self._emit("\n")

    def _end_p(self):
        self._emit("\n\n")

    def _start_a(self, attrs):
        self.href = attrs.get("href")
        self.link_text = []

    def _end_a(self):
        if self.href is None:
            return
        text, href = "".join(self.link_text), _slack_escape(self.href)
        self.href = None
        self.parts.append(f"<{href}|{text}>" if text else f"<{href}>")

    def handle_data(self, data):
        if not self.in_pre:
            # nl2br turned every line break into <br>; a newline left in the HTML is only layout.
            data = data.replace("\n", "")
        self._emit(_slack_escape(data))

    def _emit(self, text: str) -> None:
        (self.link_text if self.href is not None else self.parts).append(text)

    def result(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def _slack_escape(text: str) -> str:
    """Escape the three characters Slack reserves for its own markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
