from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def render_action(context, action):
    return mark_safe(action.render(context))


@register.simple_tag(takes_context=True)
def should_display_action(context, action):
    return action.should_display(context["request"], context["record"])
