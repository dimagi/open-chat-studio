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

    _WRAP = {"strong": "*", "b": "*", "em": "_", "i": "_", "del": "~", "s": "~", "code": "`"}
    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list] = []  # [ordered, next number] per open list
        self.href: str | None = None
        self.link_text: list[str] = []
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        if tag in self._WRAP and not self.in_pre:
            self._emit(self._WRAP[tag])
        elif tag in self._HEADINGS:
            self._emit("*")
        elif tag == "pre":
            self.in_pre = True
            self._emit("```\n")
        elif tag in ("ul", "ol"):
            self.lists.append([tag == "ol", 1])
        elif tag == "li":
            ordered, number = self.lists[-1] if self.lists else (False, 1)
            indent = "    " * max(len(self.lists) - 1, 0)
            self._emit(f"\n{indent}{f'{number}.' if ordered else '•'} ")
            if self.lists and ordered:
                self.lists[-1][1] += 1
        elif tag == "br":
            self._emit("\n")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.link_text = []

    def handle_endtag(self, tag):
        if tag in self._WRAP and not self.in_pre:
            self._emit(self._WRAP[tag])
        elif tag in self._HEADINGS:
            self._emit("*\n\n")
        elif tag == "pre":
            self.in_pre = False
            self._emit("```\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._emit("\n\n")
        elif tag == "p":
            self._emit("\n\n")
        elif tag == "a" and self.href is not None:
            text, href = "".join(self.link_text), self.href
            self.href = None
            self.parts.append(f"<{_slack_escape(href)}|{text}>" if text else f"<{_slack_escape(href)}>")

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
