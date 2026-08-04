import re
from collections import defaultdict
from enum import Enum, auto

from django.urls import URLPattern, URLResolver, get_resolver


class WafRule(Enum):
    SizeRestrictions_BODY = auto()
    NoUserAgent_HEADER = auto()

    @property
    def header(self):
        match self:
            case WafRule.SizeRestrictions_BODY:
                return (
                    "# URI patterns for endpoints that can send large POST bodies\n"
                    "# These bypass only SizeRestrictions_BODY, all other protections remain active"
                )
            case WafRule.NoUserAgent_HEADER:
                return (
                    "# URI patterns for endpoints that may not send User-Agent header\n"
                    "# These bypass only NoUserAgent_HEADER, all other protections remain active"
                )
        return None


def waf_allow(kind: WafRule):
    """Mark this view as being excluded from the specified WAF rule.

        @waf_allow(WafRule.SizeRestrictions_BODY)
        def my_view(...): ...

    To signify, "make sure the SizeRestrictions_BODY rule does not BLOCK this url pattern".

    The decorator can be applied to function-based views and class-based views.

    NOTE: This must be the top most decorator applied to the function or class.
    """

    def inner(view_func):
        if hasattr(view_func, "view_class"):
            waf_allow.views[kind].add(view_func.view_class)
        else:
            waf_allow.views[kind].add(view_func)
        return view_func

    return inner


waf_allow.views = defaultdict(set)


def get_registration_key(view_func):
    """Return the object that ``@waf_allow`` registered for a resolved view callable.

    ``resolve()`` hands back the ``as_view()`` wrapper for class-based views, but the decorator
    registers the class itself, so the two need to be reconciled before any lookup. Django sets
    ``view_class``; DRF's ViewSets set ``cls`` instead and would otherwise never match.
    """
    for attr in ("view_class", "cls"):
        view_class = getattr(view_func, attr, None)
        if view_class is not None:
            return view_class
    return view_func


def get_allowed_rules(view_func) -> set[WafRule]:
    """Return the WAF rules that ``view_func`` is marked as exempt from."""
    view = get_registration_key(view_func)
    return {rule for rule, views in waf_allow.views.items() if view in views}


def get_all_waf_patterns(resolver=None) -> dict[WafRule, list[str]]:
    """Map each WAF rule to the URI regexes of every view exempted from it.

    The URL config is walked to exhaustion *before* the registry is consulted. ``include()``
    targets import lazily, and every one of those imports can run more ``@waf_allow`` decorators,
    so reading the registry mid-walk gives an answer that depends on import order.
    """
    resolver = resolver if resolver is not None else get_resolver()
    endpoints = list(_iter_endpoints(resolver))

    patterns: dict[WafRule, set[str]] = defaultdict(set)
    for pattern_regex, view_func in endpoints:
        for rule in get_allowed_rules(view_func):
            patterns[rule].add(_convert_to_waf_regex(pattern_regex))
    # Keyed in WafRule declaration order so the generated blocks stay diff-friendly.
    return {rule: sorted(patterns[rule]) for rule in WafRule if rule in patterns}


def get_waf_patterns(rule: WafRule, resolver=None) -> list[str]:
    """Return WAF-compatible URI regexes for every view marked with ``@waf_allow(rule)``."""
    return get_all_waf_patterns(resolver).get(rule, [])


def _iter_endpoints(resolver, prefix=""):
    """Yield ``(regex, view)`` for every endpoint in the URL config, descending into includes."""
    for pattern in resolver.url_patterns:
        pattern_regex = prefix + pattern.pattern._regex.removeprefix("^")
        if isinstance(pattern, URLResolver):
            yield from _iter_endpoints(pattern, pattern_regex)
        elif isinstance(pattern, URLPattern):
            yield pattern_regex, pattern.callback


def _convert_to_waf_regex(pattern):
    """Convert Django URL pattern to AWS WAF-compatible regex"""
    pattern = re.sub(r"\?P<[^>]+>", "", pattern)
    pattern = re.sub(r"\\Z$", "$", pattern)

    # Ensure pattern starts with ^/
    if not pattern.startswith("^"):
        pattern = "^" + pattern

    if not pattern.startswith("^/"):
        pattern = "^/" + pattern[1:]

    # Ensure pattern ends with $ (unless it already has an end-of-string marker)
    if not pattern.endswith("$") and not pattern.endswith("$)"):
        pattern = pattern + "$"

    return pattern
