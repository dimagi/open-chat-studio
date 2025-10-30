import re

from django.core.management import BaseCommand
from django.urls import URLPattern, URLResolver, get_resolver
from jinja2 import Environment

from apps.web.waf import waf_allow

OUTPUT_TEMPLATE = """
{{kind.header}}
{{kind.name}} = [
{%- for regex in patterns %}
    r"{{regex}}",
{%- endfor %}
]
"""


class Command(BaseCommand):
    def handle(self, *args, **options):
        resolver = get_resolver()

        env = Environment()
        template = env.from_string(OUTPUT_TEMPLATE)

        for kind, views in waf_allow.views.items():
            patterns = []
            for view in views:
                patterns.extend(_get_patterns_for_view(resolver, view))

            # Convert to AWS WAF-compatible regexes
            waf_regexes = sorted(set(_convert_to_waf_regex(pattern) for pattern in patterns))
            output = template.render(
                {
                    "kind": kind,
                    "patterns": waf_regexes,
                }
            )
            print(output)
            print()
        print("Copy the above blocks into the ocs-deploy 'waf' module.")


def _get_patterns_for_view(resolver, target_view, prefix=""):
    """Recursively find all URL patterns that match the given view"""
    patterns = []

    for pattern in resolver.url_patterns:
        pattern_regex = pattern.pattern._regex.removeprefix("^")

        if isinstance(pattern, URLResolver):
            # Recursively search nested URL configs
            nested_prefix = prefix + pattern_regex
            patterns.extend(_get_patterns_for_view(pattern, target_view, nested_prefix))
        elif isinstance(pattern, URLPattern):
            # Check if this pattern matches our target view
            view_func = pattern.callback

            # For class-based views, check if the view_class matches
            if hasattr(view_func, "view_class") and hasattr(target_view, "as_view"):
                if view_func.view_class == target_view:
                    patterns.append(prefix + pattern_regex)

            # For function-based views, check direct equality
            elif view_func == target_view:
                patterns.append(prefix + pattern_regex)

    return patterns


def _convert_to_waf_regex(pattern):
    """Convert Django URL pattern to AWS WAF-compatible regex"""
    pattern = re.sub(r"\?P<[^>]+>", "", pattern)
    pattern = re.sub(r"\\Z$", "$", pattern)

    # Ensure pattern starts with ^
    if not pattern.startswith("^"):
        pattern = "^" + pattern

    # Ensure pattern ends with $ (unless it already has an end-of-string marker)
    if not pattern.endswith("$") and not pattern.endswith("$)"):
        pattern = pattern + "$"

    return pattern
