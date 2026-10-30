"""The standard LLM Router prompt, versioned so that a published chatbot keeps the prompt it was tested with.

Each template is a router prompt template: ``{routes}`` and any variable the user's context uses are
filled at run time like any other router prompt. ``{custom_context}`` and ``{default_route}`` are filled
first, by ``render_router_prompt``. Never edit a released version; add a new one and make it the latest.
"""

import re

ROUTER_PROMPT_TEMPLATES: dict[str, str] = {
    "v1": (
        "You are a routing assistant. Read the user's latest message, using the conversation so far for "
        "context, and choose the one route that best fits what the user wants.\n"
        "{custom_context}"
        "\n"
        "Available routes:\n"
        "{routes}\n"
        "\n"
        "Rules:\n"
        "- Choose exactly one of the routes above.\n"
        "- Judge the message by what the user is asking for, not by individual words in it.\n"
        "- If no route clearly fits, or the message could fit several, choose {default_route}."
    ),
}

LATEST_ROUTER_PROMPT_VERSION = "v1"

_PLACEHOLDERS = re.compile(r"\{(custom_context|default_route)\}")


def render_router_prompt(version: str, custom_context: str, default_route: str) -> str:
    """The prompt template for ``version`` with the context and default route filled in.

    The context is the user's own prompt text and is inserted as written, so its variables are filled
    at run time. The default route is a keyword, which is not a template, so its braces are escaped.

    The builder previews the prompt with ``renderRouterPromptPreview``; both are tested against the cases
    in ``apps/pipelines/tests/data/router_prompt_preview_cases.json``.
    """
    context = custom_context.strip()
    values = {
        "custom_context": f"\nAdditional context:\n{context}\n" if context else "",
        "default_route": default_route.replace("{", "{{").replace("}", "}}"),
    }
    # One pass, so a value is never scanned for placeholders after it is inserted.
    return _PLACEHOLDERS.sub(lambda match: values[match.group(1)], ROUTER_PROMPT_TEMPLATES[version])


def router_prompt_templates_data() -> dict:
    """The templates and the latest version, for the pipeline builder's prompt preview."""
    return {"latest": LATEST_ROUTER_PROMPT_VERSION, "templates": ROUTER_PROMPT_TEMPLATES}
