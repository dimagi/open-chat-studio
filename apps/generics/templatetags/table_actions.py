from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def render_action(context, action):
    return mark_safe(action.render(context))


@register.simple_tag(takes_context=True)
def action_allowed(context, action):
    if not action.required_permissions:
        return True
    return context["request"].user.has_perms(action.required_permissions)
