"""Fetch AWS WAF telemetry.

Two sources are read here, both read-only:

* CloudWatch Logs Insights, for the requests the WAF blocked (or would have blocked, since
  ocs-deploy runs the managed rule group in Count mode).
* The wafv2 API, for the regex pattern sets ocs-deploy has actually deployed. Comparing a
  blocked URI against those tells us whether a missing exemption is a code change or just a
  pending deploy.
"""

import re
import time
from dataclasses import dataclass

from apps.web.waf import WafRule

LOG_GROUP_PREFIX = "aws-waf-logs-"

# ocs-deploy names its pattern sets "<env>-LargeBodyPaths<n>" / "<env>-NoUserAgentPaths<n>",
# one set per rule per group of compacted regexes.
PATTERN_SET_RULES = {
    "LargeBodyPaths": WafRule.SizeRestrictions_BODY,
    "NoUserAgentPaths": WafRule.NoUserAgent_HEADER,
}

# A rule can fire in three places: inside a managed rule group, as a Count-mode rule on the
# web ACL itself, or as the terminating rule. Coalesce them so one row shape covers all three.
_RULE_FIELDS = """
fields httpRequest.uri as uri,
       httpRequest.httpMethod as method,
       coalesce(ruleGroupList.0.terminatingRule.ruleId,
                nonTerminatingMatchingRules.0.ruleId,
                terminatingRuleId) as rule,
       coalesce(ruleGroupList.0.terminatingRule.action,
                nonTerminatingMatchingRules.0.action,
                action) as ruleAction
| filter ispresent(rule) and rule != 'Default_Action'
"""

DETAIL_QUERY = (
    _RULE_FIELDS
    + """
| stats count(*) as hits,
        count_distinct(httpRequest.clientIp) as uniqueIPs,
        count_distinct(httpRequest.country) as uniqueCountries,
        fromMillis(earliest(@timestamp)) as firstSeen,
        fromMillis(latest(@timestamp)) as lastSeen
    by uri, method, rule, ruleAction
| sort hits desc
"""
)

TOTALS_QUERY = (
    _RULE_FIELDS
    + """
| stats count(*) as hits,
        count_distinct(httpRequest.uri) as uniquePaths,
        count_distinct(httpRequest.clientIp) as uniqueIPs
    by rule, ruleAction
| sort hits desc
"""
)


class WafLogsError(Exception):
    """Raised when AWS cannot give us the data we asked for."""


@dataclass
class LogRow:
    """One aggregated (uri, method, rule) group from Logs Insights."""

    uri: str
    method: str
    rule: str
    action: str
    hits: int
    unique_ips: int
    unique_countries: int
    first_seen: str
    last_seen: str

    @classmethod
    def from_result(cls, row: dict[str, str]) -> "LogRow":
        return cls(
            uri=row.get("uri", ""),
            method=row.get("method", ""),
            rule=row.get("rule", ""),
            action=row.get("ruleAction", ""),
            hits=_to_int(row.get("hits")),
            unique_ips=_to_int(row.get("uniqueIPs")),
            unique_countries=_to_int(row.get("uniqueCountries")),
            first_seen=row.get("firstSeen", ""),
            last_seen=row.get("lastSeen", ""),
        )

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def get_session(profile: str | None = None, region: str | None = None):
    import boto3  # noqa: PLC0415 - TID253: heavy lib, slow startup

    return boto3.Session(profile_name=profile, region_name=region)


def aws_errors() -> tuple[type[Exception], ...]:
    """AWS failures worth turning into a message instead of a traceback (expired SSO, no perms)."""
    from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415 - lazy: used with boto3

    return (BotoCoreError, ClientError)


def find_log_group(session) -> str:
    """Locate the WAF log group. WAF requires the ``aws-waf-logs-`` prefix, so this is reliable."""
    client = session.client("logs")
    paginator = client.get_paginator("describe_log_groups")
    names = [
        group["logGroupName"]
        for page in paginator.paginate(logGroupNamePrefix=LOG_GROUP_PREFIX)
        for group in page["logGroups"]
    ]

    if not names:
        raise WafLogsError(
            f"No log group starting with '{LOG_GROUP_PREFIX}' found in this account/region. "
            f"Check your --profile and --region, or pass --log-group explicitly."
        )
    if len(names) > 1:
        joined = "\n  ".join(sorted(names))
        raise WafLogsError(f"Multiple WAF log groups found, pass --log-group to pick one:\n  {joined}")
    return names[0]


def run_insights_query(session, log_group, query, start, end, limit=5000, timeout=300, poll_interval=1.0):
    """Run a Logs Insights query to completion and return its rows as dicts."""
    client = session.client("logs")
    query_id = client.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
        limit=limit,
    )["queryId"]

    deadline = time.monotonic() + timeout
    while True:
        result = client.get_query_results(queryId=query_id)
        status = result["status"]
        if status == "Complete":
            return [{field["field"]: field["value"] for field in row} for row in result["results"]]
        if status in ("Failed", "Cancelled", "Timeout"):
            raise WafLogsError(f"CloudWatch Logs Insights query {status.lower()} for log group {log_group}")
        if time.monotonic() > deadline:
            client.stop_query(queryId=query_id)
            raise WafLogsError(
                f"Query did not finish within {timeout}s. Try a shorter --since window or a lower --limit."
            )
        time.sleep(poll_interval)


def fetch_events(session, log_group, start, end, limit=5000) -> list[LogRow]:
    rows = run_insights_query(session, log_group, DETAIL_QUERY, start, end, limit=limit)
    return [LogRow.from_result(row) for row in rows]


def fetch_totals(session, log_group, start, end) -> list[dict]:
    """Per-rule totals, used to report how much the detail query's limit left out."""
    rows = run_insights_query(session, log_group, TOTALS_QUERY, start, end, limit=1000)
    return [
        {
            "rule": row.get("rule", ""),
            "action": row.get("ruleAction", ""),
            "hits": _to_int(row.get("hits")),
            "unique_paths": _to_int(row.get("uniquePaths")),
            "unique_ips": _to_int(row.get("uniqueIPs")),
        }
        for row in rows
    ]


def fetch_deployed_patterns(session) -> dict[WafRule, list[str]]:
    """Return the regex strings currently deployed in each of the WAF's scope-down pattern sets."""
    client = session.client("wafv2")
    deployed: dict[WafRule, list[str]] = {}

    for summary in _list_regex_pattern_sets(client):
        rule = _rule_for_pattern_set(summary["Name"])
        if rule is None:
            continue
        detail = client.get_regex_pattern_set(Name=summary["Name"], Scope="REGIONAL", Id=summary["Id"])
        regexes = [entry["RegexString"] for entry in detail["RegexPatternSet"]["RegularExpressionList"]]
        deployed.setdefault(rule, []).extend(regexes)

    return deployed


def _list_regex_pattern_sets(client):
    marker = None
    while True:
        kwargs = {"Scope": "REGIONAL", "Limit": 100}
        if marker:
            kwargs["NextMarker"] = marker
        response = client.list_regex_pattern_sets(**kwargs)
        yield from response.get("RegexPatternSets", [])
        marker = response.get("NextMarker")
        if not marker:
            return


def _rule_for_pattern_set(name: str) -> WafRule | None:
    for fragment, rule in PATTERN_SET_RULES.items():
        if fragment in name:
            return rule
    return None


def compile_deployed_patterns(deployed: dict[WafRule, list[str]]) -> tuple[dict[WafRule, list], list[str]]:
    """Compile deployed regexes with Python's ``re``, returning any that could not be compiled.

    AWS WAF evaluates these with RE2. The subset ocs-deploy generates is compatible with ``re``,
    but anything exotic added by hand in the console could fail here, so report rather than hide it.
    """
    compiled: dict[WafRule, list] = {}
    errors = []
    for rule, regexes in deployed.items():
        for regex in regexes:
            try:
                compiled.setdefault(rule, []).append(re.compile(regex))
            except re.error as exc:
                errors.append(f"{rule.name}: {regex!r} ({exc})")
    return compiled, errors
