from django import template

register = template.Library()


@register.simple_tag
def participant_sessions(experiment, participant):
    return experiment.sessions.filter(participant=participant)
