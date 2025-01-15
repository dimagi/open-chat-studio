from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe


def render_help_with_link(help_content: str, docs_link: str):
    """
    Utility for rendering field help text with a link to the documentation.

    Args:
        help_content (str): The help text.
        docs_link (str, optional): The relative URL to the documentation (relative to the documentation base URL)
    """
    if not docs_link.startswith("http"):
        docs_link = settings.DOCUMENTATION_LINKS[docs_link]

    if not docs_link.startswith("http"):
        docs_link = f"{settings.DOCUMENTATION_BASE_URL}{docs_link}"

    help_content = mark_safe(help_content)
    return format_html(
        """{help_content}<p><a class="link" href="{docs_link}" target="_blank">Learn more</a></p>""",
        docs_base_url=settings.DOCUMENTATION_BASE_URL,
        docs_link=docs_link,
        help_content=help_content,
    )
