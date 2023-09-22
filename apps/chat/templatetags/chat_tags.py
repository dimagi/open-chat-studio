import markdown
from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.safestring import mark_safe
from markdown.extensions.fenced_code import FencedCodeExtension

register = template.Library()


@register.filter
def render_markdown(text):
    return linebreaksbr(mark_safe(markdown.markdown(text, extensions=[FencedCodeExtension()])))
