from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def single_quotes(text):
    return mark_safe(text.replace('"', "'"))
