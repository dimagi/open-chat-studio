from django import template
from django.urls import reverse

from apps.service_providers.utils import ServiceProvider, get_available_subtypes

register = template.Library()


@register.simple_tag()
def service_provider(provider_type_slug):
    """Return the ``ServiceProvider`` enum member for the given slug."""
    return ServiceProvider[provider_type_slug]


@register.simple_tag(takes_context=True)
def service_provider_subtype_choices(context, provider_type_slug):
    """Return a list of ``(label, url)`` tuples for available subtypes.

    Used to build the "Add new" dropdown for service provider home.
    """
    request = context["request"]
    provider = ServiceProvider[provider_type_slug]
    return [
        (
            str(subtype.label),
            reverse(
                "service_providers:new",
                kwargs={
                    "team_slug": request.team.slug,
                    "provider_type": provider.slug,
                    "subtype": str(subtype),
                },
            ),
        )
        for subtype in get_available_subtypes(provider, request)
    ]
