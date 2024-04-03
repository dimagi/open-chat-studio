from django import template

from apps.utils.time import seconds_to_human as seconds_to_human_util

register = template.Library()


@register.filter
def seconds_to_human(value):
    return seconds_to_human_util(value)
