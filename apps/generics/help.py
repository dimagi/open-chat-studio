from django.conf import settings
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe


def render_field_help(help_content, docs_link=None):
    """
    Renders the help content for a field as a help icon with dropdown.

    Args:
        help_content (str): The help text.
        docs_link (str, optional): The relative URL to the documentation (relative to the documentation base URL)
    """
    return render_to_string(
        "generic/help.html",
        {
            "help_content": mark_safe(help_content),
            "docs_link": docs_link,
            "docs_base_url": settings.DOCUMENTATION_BASE_URL,
        },
    )
