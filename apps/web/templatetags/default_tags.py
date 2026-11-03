from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

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


@register.filter
def with_classes(attrs, classes):
    """Render a cotton component's pass-through attributes with `classes` prepended to any `class` the caller gave.

    Used on a component's root element in place of `{{ attrs }}` when the element has classes of its own,
    so `<c-ui.button class="w-full">` produces one merged `class` attribute. A `True` value renders a bare
    attribute; `False` and `None` omit it, so `:checked="flag"` behaves like a boolean HTML attribute.
    """
    items = dict(attrs.attrs_dict()) if hasattr(attrs, "attrs_dict") else dict(attrs)
    caller_classes = str(items.pop("class", "") or "")
    merged = " ".join(classes.split() + caller_classes.split())
    rendered = [format_html('class="{}"', merged)]
    for key, value in items.items():
        if value is True:
            rendered.append(format_html("{}", key))
        elif value is not False and value is not None:
            rendered.append(format_html('{}="{}"', key, value))
    return mark_safe(" ".join(rendered))
