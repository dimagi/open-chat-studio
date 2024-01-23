import markdown
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

from apps.utils.markdown import ResourceExtension

register = template.Library()


@register.filter
@stringfilter
def render_markdown(value):
    if not value:
        return ""
    md = markdown.Markdown(extensions=["fenced_code", "tables", "footnotes", ResourceExtension()])
    return mark_safe(md.convert(value))
