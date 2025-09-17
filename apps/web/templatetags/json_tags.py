import json

from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.http import QueryDict
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def to_json(obj):
    """Source: https://gist.github.com/czue/90e287c9818ae726f73f5850c1b00f7f"""

    def escape_script_tags(unsafe_str):
        # seriously: http://stackoverflow.com/a/1068548/8207
        return unsafe_str.replace("</script>", '<" + "/script>')

    # json.dumps does not properly convert QueryDict array parameter to json
    if isinstance(obj, QueryDict):
        obj = dict(obj)
    return mark_safe(escape_script_tags(json.dumps(obj, cls=DjangoJSONEncoder)))


@register.filter
def prettyjson(obj):
    """Convert a Python object to pretty-formatted JSON string."""
    if obj is None:
        return "{}"

    # json.dumps does not properly convert QueryDict array parameter to json
    if isinstance(obj, QueryDict):
        obj = dict(obj)

    try:
        return json.dumps(obj, cls=DjangoJSONEncoder, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        # If object is not JSON serializable, return string representation
        return str(obj)
