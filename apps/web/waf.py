from collections import defaultdict
from enum import Enum, auto


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

    def inner(fn):
        waf_allow.views[kind].add(fn)
        return fn

    return inner


waf_allow.views = defaultdict(set)
