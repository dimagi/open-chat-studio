import re

from django import template

register = template.Library()


@register.filter
def matches(value, pattern):
    if value is None:
        return False
    return bool(re.match(pattern, str(value)))
