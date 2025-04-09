from copy import deepcopy

import markdown
import nh3
from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.safestring import mark_safe
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.footnotes import FootnoteExtension
from markdown.extensions.tables import TableExtension

from apps.utils.markdown import FileExtension

register = template.Library()


@register.filter
def render_markdown(text):
    if not text:
        return ""

    text = markdown.markdown(
        text, extensions=[FootnoteExtension(BACKLINK_TEXT=""), FencedCodeExtension(), FileExtension(), TableExtension()]
    )
    attributes = deepcopy(nh3.ALLOWED_ATTRIBUTES)
    attributes["code"] = {"class"}
    attributes["a"] = {"href", "title", "target"}  # Allows link to open in new tab
    cleaned_html = nh3.clean(text, attributes=attributes)
    cleaned_html = cleaned_html.replace(">\n<", "><")
    cleaned_html = cleaned_html.replace("</p><p>", "</p>\n<p>")
    return linebreaksbr(mark_safe(cleaned_html))
