from django import template

from apps.teams.roles import is_admin

register = template.Library()


@register.filter
def is_admin_of(user, team):
    return is_admin(user, team)


@register.simple_tag(takes_context=True)
def has_perm(context, app_label, permission):
    request = context["request"]
    return request.user.has_perm(f"{app_label}.{permission}")
