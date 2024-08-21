import markdown
from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.safestring import mark_safe
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension

from apps.utils.markdown import FileExtension

register = template.Library()


@register.filter
def render_markdown(text):
    if not text:
        return ""

    text = markdown.markdown(text, extensions=[FencedCodeExtension(), FileExtension(), TableExtension()])
    text = text.replace(">\n<", "><")
    text = text.replace("</p><p>", "</p>\n<p>")
    return linebreaksbr(mark_safe(text))
