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


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    return dictionary.get(key)


@register.filter
def get_attr(obj, field_name):
    """Retrieves the value of a field from an object dynamically."""
    if field_name.startswith("_"):
        raise ValueError(f"{field_name} not allowed")
    return getattr(obj, field_name, None)
