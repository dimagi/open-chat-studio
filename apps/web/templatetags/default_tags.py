from django import template

register = template.Library()


@register.simple_tag
def define(val=None):
    return val


@register.filter(name="times")
def times(number):
    return range(number)
