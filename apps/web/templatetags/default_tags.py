from django import template

register = template.Library()


@register.simple_tag
def define(val=None):
    return val


@register.filter(name="times")
def times(number):
    return range(number)


@register.simple_tag(takes_context=True)
def absolute_url(context, relative_url):
    request = context["request"]
    return request.build_absolute_uri(relative_url)
