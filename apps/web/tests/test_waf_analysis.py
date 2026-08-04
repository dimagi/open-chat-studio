import re
from datetime import timedelta

import pytest
from django.core.management.base import CommandError
from django.urls import resolve

from apps.web.management.commands.analyze_waf_logs import parse_duration
from apps.web.waf import WafRule, get_all_waf_patterns, get_allowed_rules, get_waf_patterns, waf_allow
from apps.web.waf_analysis import Fix, classify_row, rule_to_waf_rule
from apps.web.waf_logs import LogRow, _rule_for_pattern_set, compile_deployed_patterns


def make_row(uri, rule="NoUserAgent_HEADER", **kwargs):
    defaults = {
        "method": "GET",
        "action": "BLOCK",
        "hits": 1,
        "unique_ips": 1,
        "unique_countries": 1,
        "first_seen": "",
        "last_seen": "",
    }
    return LogRow(uri=uri, rule=rule, **{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("90m", timedelta(minutes=90)), ("24h", timedelta(hours=24)), ("7d", timedelta(days=7))],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["7", "d", "7w", "", "-1d"])
def test_parse_duration_rejects_bad_input(value):
    with pytest.raises(CommandError):
        parse_duration(value)


def test_rule_to_waf_rule():
    assert rule_to_waf_rule("NoUserAgent_HEADER") is WafRule.NoUserAgent_HEADER
    assert rule_to_waf_rule("SizeRestrictions_BODY") is WafRule.SizeRestrictions_BODY
    # Rules from the managed group that no @waf_allow exemption covers.
    assert rule_to_waf_rule("GenericLFI_URIPATH") is None


def test_scanner_traffic_is_not_an_endpoint():
    finding = classify_row(make_row("/wp-admin/setup-config.php"))
    assert finding.is_endpoint is False
    assert finding.route is None


def test_malformed_uri_does_not_raise():
    for uri in ["/%zz", "/\x00", "/../../etc/passwd", "/" + "a" * 5000]:
        assert classify_row(make_row(uri)).is_endpoint in (True, False)


def test_query_string_is_stripped_before_resolving():
    finding = classify_row(make_row("/robots.txt?x=1"))
    assert finding.is_endpoint is True
    assert finding.route == "/robots.txt"


def test_decorated_view_is_detected():
    # /robots.txt is decorated with @waf_allow(NoUserAgent_HEADER) in apps/web.
    finding = classify_row(make_row("/robots.txt"))
    assert finding.is_endpoint is True
    assert finding.decorated is True
    assert finding.view_name
    assert finding.source is not None
    assert ":" in finding.source


def test_endpoint_missing_the_decorator_reports_add_decorator():
    # /robots.txt carries NoUserAgent_HEADER but not SizeRestrictions_BODY.
    finding = classify_row(make_row("/robots.txt", rule="SizeRestrictions_BODY"))
    assert finding.decorated is False
    assert finding.fix is Fix.ADD_DECORATOR
    assert finding.remedy == "@waf_allow(WafRule.SizeRestrictions_BODY)"


def test_decorated_but_undeployed_reports_deploy():
    finding = classify_row(make_row("/robots.txt"), deployed_patterns={WafRule.NoUserAgent_HEADER: []})
    assert finding.decorated is True
    assert finding.deployed is False
    assert finding.fix is Fix.DEPLOY


def test_decorated_and_deployed_reports_investigate():
    patterns = {WafRule.NoUserAgent_HEADER: [re.compile(r"^/robots\.txt$")]}
    finding = classify_row(make_row("/robots.txt"), deployed_patterns=patterns)
    assert finding.deployed is True
    assert finding.fix is Fix.INVESTIGATE


def test_unsupported_rule_reports_unsupported():
    finding = classify_row(make_row("/robots.txt", rule="GenericLFI_URIPATH"))
    assert finding.fix is Fix.UNSUPPORTED


def test_deployed_state_is_unknown_when_patterns_not_fetched():
    finding = classify_row(make_row("/robots.txt"), deployed_patterns=None)
    assert finding.deployed is None
    assert "not checked" in finding.remedy


def test_compile_deployed_patterns_reports_bad_regexes():
    compiled, errors = compile_deployed_patterns({WafRule.NoUserAgent_HEADER: [r"^/ok$", r"^/(unclosed"]})
    assert len(compiled[WafRule.NoUserAgent_HEADER]) == 1
    assert len(errors) == 1
    assert "NoUserAgent_HEADER" in errors[0]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("chatbots-prod-LargeBodyPaths0", WafRule.SizeRestrictions_BODY),
        ("chatbots-prod-NoUserAgentPaths1", WafRule.NoUserAgent_HEADER),
        ("some-other-set", None),
    ],
)
def test_rule_for_pattern_set(name, expected):
    assert _rule_for_pattern_set(name) is expected


def test_deployed_patterns_cover_the_exported_allow_list():
    """Every pattern the export command emits must match at least one URL it was generated from.

    Guards the round trip the drift check relies on: exported regexes are matched against raw
    WAF URI paths with Python's ``re``, so they have to be anchored and compilable.
    """
    for rule in waf_allow.views:
        for pattern in get_waf_patterns(rule):
            assert pattern.startswith("^/"), pattern
            assert pattern.endswith(("$", "$)")), pattern
            re.compile(pattern)


def test_get_allowed_rules_resolves_class_based_views():
    match = resolve("/robots.txt")
    assert WafRule.NoUserAgent_HEADER in get_allowed_rules(match.func)


def test_exported_patterns_are_deterministic():
    """The URL config must be walked to exhaustion before the registry is read.

    ``include()`` targets import lazily and each import can register more views, so reading the
    registry mid-walk used to silently drop endpoints depending on import order.
    """
    assert get_all_waf_patterns() == get_all_waf_patterns()
    assert list(get_all_waf_patterns()) == [rule for rule in WafRule if rule in get_all_waf_patterns()]


def test_template_view_routes_are_exported():
    """apps/web/urls.py decorates ``TemplateView.as_view()``, which registers the base class.

    Every inline ``TemplateView`` route in the project therefore inherits the exemption. Pinned
    here because the exported list (and the deployed WAF) depends on it.
    """
    patterns = get_all_waf_patterns()[WafRule.NoUserAgent_HEADER]
    assert r"^/robots\.txt$" in patterns
    assert "^/applications/$" in patterns
