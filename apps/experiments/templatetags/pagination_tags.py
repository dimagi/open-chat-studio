from django import template

register = template.Library()


@register.filter
def multiply(value, arg):
    return int(value) * int(arg)


@register.filter
def subtract(value, arg):
    return int(value) - int(arg)


@register.filter
def minimum(value, arg):
    return min(int(value), int(arg))
