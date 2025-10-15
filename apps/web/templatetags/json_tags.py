import json
from json import JSONDecodeError

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
    try:
        json_string = json.dumps(obj, indent=2, cls=DjangoJSONEncoder)
        return mark_safe(escape_script_tags(json_string))
    except JSONDecodeError:
        return mark_safe("Unable to decode JSON data")
