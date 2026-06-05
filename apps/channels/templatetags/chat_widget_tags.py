from django import template

from apps.channels import widget_versions

register = template.Library()


@register.simple_tag
def widget_script_url():
    return widget_versions.widget_script_url()
