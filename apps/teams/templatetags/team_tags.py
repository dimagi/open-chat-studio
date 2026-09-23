from django import template

from apps.teams.export.selection import selected_experiment_count

register = template.Library()


@register.simple_tag(takes_context=True)
def has_perm(context, app_label, permission):
    """Template tag to check dynamic permissions:

    {% load team_tags %}
    {% has_perm app_label perm_name as has_permission %}
    {% if has_permission %}
        ...
    {% endif %}
    """
    request = context["request"]
    return request.user.has_perm(f"{app_label}.{permission}")


@register.simple_tag
def exportable_chatbot_count(team) -> int:
    return selected_experiment_count(team)
