from django import template
from django.urls import reverse

from apps.service_providers.utils import ServiceProvider, get_available_subtypes

register = template.Library()


@register.simple_tag()
def service_provider(provider_type_slug):
    """Return the ``ServiceProvider`` enum member for the given slug."""
    return ServiceProvider[provider_type_slug]


@register.inclusion_tag("service_providers/components/usage_version_pill.html")
def usage_version_pill(obj):
    """Render ``obj``'s badge for a row of the provider usages list.

    The badge names the object's place in its version family. Unversioned objects
    have no place to name, so they only get a badge when archived.

    Version snapshots link to their own row in the versions table, but only for
    models whose ``get_absolute_url`` is version-aware (chatbots today — see
    ``VersionsMixin.has_version_specific_url``). The working version gets no link
    because the row's name already points there.

    ``version_number`` is read directly rather than via ``get_version_name``: the
    latter raises AttributeError on ``VersionsMixin`` subclasses that don't define
    it (e.g. DocumentSource).
    """
    version_number = getattr(obj, "version_number", None)
    archived = bool(getattr(obj, "is_archived", False))
    if getattr(obj, "is_working_version", False):
        label, style = "working version", "badge-info"
    elif getattr(obj, "is_default_version", False):
        label, style = f"published v{version_number}", "badge-success"
    elif version_number:
        label, style = f"v{version_number}", "badge-ghost"
    elif archived:
        label, style, archived = "archived", "badge-warning", False
    else:
        return {"label": "", "classes": "", "url": None, "title": ""}

    deep_links = getattr(obj, "is_a_version", False) and getattr(obj, "has_version_specific_url", False)
    return {
        "label": label,
        "classes": f"badge {style} badge-sm ml-1" + (" line-through opacity-60" if archived else ""),
        "url": obj.get_absolute_url() if deep_links else None,
        "title": "archived" if archived else "",
    }


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
