"""Classify WAF log rows against the Django URL config.

Two questions matter when reading WAF logs:

1. Is this a real endpoint or a scanner? A URI that resolves to a view is a false positive we
   need to fix; one that doesn't is the WAF doing its job.
2. If it is a real endpoint, what fixes it? Either the view is missing ``@waf_allow``, or it has
   the decorator but the exported patterns haven't been deployed yet. Those need different work,
   so they are distinguished rather than lumped into "look into this".
"""

import contextlib
import inspect
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.urls import Resolver404, resolve

from apps.web.waf import WafRule, get_allowed_rules, get_registration_key
from apps.web.waf_logs import LogRow

# Rules the WebACL defines itself that fire on something other than the request path, so no
# per-view exemption can address them. Keyed by rule id, valued by what to do instead.
NON_PATH_RULES = {
    "RateLimitRule": "rate limit on the client IP — raise the limit in ocs-deploy if this traffic is legitimate",
    "BlockTempIPs": "IP blocklist match — manage the IP set in ocs-deploy",
    "BlockPermanentIPs": "IP blocklist match — managed via the AWS console",
}


class Fix(Enum):
    """What needs to happen for a blocked endpoint to stop being blocked."""

    ADD_DECORATOR = "Add @waf_allow, then export and deploy"
    DEPLOY = "Decorated in code but not deployed — export and deploy"
    INVESTIGATE = "Decorated and deployed, yet still matched"
    UNSUPPORTED = "No @waf_allow rule covers this WAF rule"
    NOT_PATH_BASED = "Not a path-based rule — no per-view exemption applies"


@dataclass
class Finding:
    row: LogRow
    # How many distinct URIs collapsed into this finding (see ``deduplicate_endpoints``).
    uri_count: int = 1
    route: str | None = None
    view_name: str | None = None
    source: str | None = None
    waf_rule: WafRule | None = None
    decorated: bool = False
    # None when the deployed pattern sets were not fetched (--no-drift, or no AWS access).
    deployed: bool | None = None

    @property
    def is_endpoint(self) -> bool:
        return self.route is not None

    @property
    def fix(self) -> Fix:
        if self.row.rule in NON_PATH_RULES:
            return Fix.NOT_PATH_BASED
        if self.waf_rule is None:
            return Fix.UNSUPPORTED
        if not self.decorated:
            return Fix.ADD_DECORATOR
        if self.deployed is False:
            return Fix.DEPLOY
        return Fix.INVESTIGATE

    @property
    def remedy(self) -> str:
        if self.fix is Fix.ADD_DECORATOR:
            return f"@waf_allow(WafRule.{self.waf_rule.name})"
        if self.fix is Fix.NOT_PATH_BASED:
            return NON_PATH_RULES[self.row.rule]
        if self.fix is Fix.INVESTIGATE and self.deployed is None:
            return "decorated (deployed state not checked)"
        return self.fix.value

    def as_dict(self) -> dict:
        return {
            **self.row.as_dict(),
            "outcome": self.row.outcome,
            "uri_count": self.uri_count,
            "route": self.route or "",
            "view": self.view_name or "",
            "source": self.source or "",
            "fix": self.fix.name if self.is_endpoint else "",
        }


def classify(rows: list[LogRow], deployed_patterns: dict[WafRule, list] | None = None) -> list[Finding]:
    return [classify_row(row, deployed_patterns) for row in rows]


def deduplicate_endpoints(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that share a Django route, rule and method.

    Logs Insights aggregates by URI, so a route with a variable segment produces one row per
    session id or UUID seen. They all resolve to the same view and need the same fix, so
    reporting them separately just buries the finding under its own repetitions.
    """
    merged: dict[tuple, Finding] = {}
    for finding in findings:
        key = (finding.route, finding.row.method, finding.row.rule, finding.row.outcome)
        existing = merged.get(key)
        if existing is None:
            merged[key] = replace(finding, row=replace(finding.row), uri_count=1)
        else:
            _absorb(existing, finding)
    return list(merged.values())


def _absorb(target: Finding, other: Finding) -> None:
    into, extra = target.row, other.row
    if extra.hits > into.hits:
        into.uri = extra.uri  # keep the busiest URI as the worked example
    into.hits += extra.hits
    # Distinct-IP counts can overlap between URIs, so the max is a lower bound rather than a sum.
    into.unique_ips = max(into.unique_ips, extra.unique_ips)
    into.unique_countries = max(into.unique_countries, extra.unique_countries)
    into.first_seen = min(filter(None, (into.first_seen, extra.first_seen)), default="")
    into.last_seen = max(filter(None, (into.last_seen, extra.last_seen)), default="")
    target.uri_count += 1


def classify_row(row: LogRow, deployed_patterns: dict[WafRule, list] | None = None) -> Finding:
    finding = Finding(row=row, waf_rule=rule_to_waf_rule(row.rule))

    # WAF matches on the raw URI path; Django resolves the percent-decoded one.
    raw_path = urlsplit(row.uri).path
    match = resolve_view(raw_path)
    if match is None:
        return finding

    # Django routes are relative to the URLconf root; WAF logs are absolute.
    finding.route = "/" + match.route.lstrip("/")
    finding.view_name = view_name(match.func)
    finding.source = view_source(match.func)
    if finding.waf_rule is not None:
        finding.decorated = finding.waf_rule in get_allowed_rules(match.func)
        finding.deployed = is_covered(raw_path, finding.waf_rule, deployed_patterns)
    return finding


def rule_to_waf_rule(rule_id: str) -> WafRule | None:
    """Map a WAF rule id (e.g. ``NoUserAgent_HEADER``) to the ``WafRule`` we can exempt views from."""
    return WafRule.__members__.get(rule_id)


def resolve_view(raw_path: str):
    try:
        return resolve(unquote(raw_path))
    except (Resolver404, UnicodeDecodeError, ValueError):
        # Scanner traffic routinely contains malformed percent-encoding and null bytes.
        return None


def is_covered(raw_path: str, waf_rule: WafRule, deployed_patterns: dict[WafRule, list] | None) -> bool | None:
    """Does the deployed WAF scope-down allow list already match this path?"""
    if deployed_patterns is None:
        return None
    return any(pattern.search(raw_path) for pattern in deployed_patterns.get(waf_rule, []))


def view_name(view_func) -> str:
    view = get_registration_key(view_func)
    qualname = getattr(view, "__qualname__", "")
    # DRF builds a WrappedAPIView class inside a closure, so its qualname is noise; the __name__
    # it copies from the wrapped function is what you'd actually grep for.
    if qualname and "<locals>" not in qualname:
        return qualname
    return getattr(view, "__name__", None) or repr(view)


def view_source(view_func) -> str | None:
    """Return ``path:line`` for the view, relative to the repo root where possible."""
    view = get_registration_key(view_func)
    try:
        filename = inspect.getsourcefile(view)
        _, line = inspect.getsourcelines(view)
    except (OSError, TypeError):
        # Dynamically built classes (DRF's WrappedAPIView) have no source of their own, but they
        # do carry the defining module, which is enough to find the view by name.
        filename, line = _defining_module_file(view), None
    if not filename:
        return None

    path = Path(filename)
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        with contextlib.suppress(ValueError):
            path = path.relative_to(Path(base_dir))
    return f"{path}:{line}" if line else str(path)


def _defining_module_file(view) -> str | None:
    module = sys.modules.get(getattr(view, "__module__", ""))
    return getattr(module, "__file__", None)
