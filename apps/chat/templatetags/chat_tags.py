import markdown
from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.safestring import mark_safe
from markdown.extensions.fenced_code import FencedCodeExtension

from apps.utils.markdown import FileExtension

register = template.Library()


@register.filter
def render_markdown(text):
    if not text:
        return ""
    text = markdown.markdown(text, extensions=[FencedCodeExtension(), FileExtension()])
    text = text.replace("</li>\n<li>", "</li><li>")
    text = text.replace("<ol>\n<li>", "<ol><li>")
    text = text.replace("</li>\n</ol>", "</li></ol>")
    text = text.replace("<li>\n<p>", "<li><p>")
    text = text.replace("</p>\n</li>", "</p></li>")
    return linebreaksbr(mark_safe(text))
