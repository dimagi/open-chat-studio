from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe


def render_help_with_link(help_content: str, docs_link: str, link_text="Learn more", line_break=True):
    """
    Utility for rendering field help text with a link to the documentation.

    Args:
        help_content (str): The help text.
        docs_link (str, optional): The relative URL to the documentation (relative to the documentation base URL).
        link_text (str, optional): The text to be displayed in the link.
        line_break (bool, optional): Whether to enclose the link in a paragraph or not.
    """
    if not docs_link.startswith("http"):
        docs_link = settings.DOCUMENTATION_LINKS[docs_link]

    if not docs_link.startswith("http"):
        docs_link = f"{settings.DOCUMENTATION_BASE_URL}{docs_link}"

    help_content = mark_safe(help_content)
    link_template = '<a class="link" href="{docs_link}" target="_blank">{link_text}</a>'
    if line_break:
        link_template = f"<p>{link_template}</p>"
    return format_html(
        f"""{{help_content}}{link_template}""",
        docs_base_url=settings.DOCUMENTATION_BASE_URL,
        docs_link=docs_link,
        help_content=help_content,
        link_text=link_text,
    )
