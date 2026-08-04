"""Pull WAF logs from CloudWatch and report which blocked requests are real endpoints.

Replaces the old manual loop of running a Logs Insights query in the console, exporting a CSV,
and hand-matching paths against the URL config.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils.functional import cached_property

from apps.web.waf import WafRule
from apps.web.waf_analysis import Finding, Fix, classify, classify_row, deduplicate_endpoints
from apps.web.waf_logs import (
    LogRow,
    WafLogsError,
    aws_errors,
    compile_deployed_patterns,
    fetch_deployed_patterns,
    fetch_events,
    fetch_totals,
    find_log_group,
    get_session,
)

DURATION_RE = re.compile(r"^(\d+)([mhd])$")
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}

CSV_FIELDS = [
    "hits",
    "outcome",
    "action",
    "effective_action",
    "rule",
    "method",
    "uri",
    "uri_count",
    "route",
    "view",
    "source",
    "fix",
    "unique_ips",
    "unique_countries",
    "first_seen",
    "last_seen",
]


class Command(BaseCommand):
    help = "Query AWS WAF logs and report which blocked requests are legitimate Django endpoints"

    def add_arguments(self, parser):
        parser.add_argument("--since", default="7d", help="Look-back window: 90m, 24h, 7d (default: 7d)")
        parser.add_argument("--log-group", help="WAF log group name (auto-discovered by default)")
        parser.add_argument("--profile", help="AWS profile name (e.g. ocs-prod)")
        parser.add_argument("--region", help="AWS region (defaults to the profile's region)")
        parser.add_argument("--limit", type=int, default=5000, help="Max aggregated rows to fetch (default: 5000)")
        parser.add_argument("--min-hits", type=int, default=1, help="Ignore rows below this hit count")
        parser.add_argument("--no-drift", action="store_true", help="Skip the deployed WAF pattern set check")
        parser.add_argument("--attacks", type=int, default=10, help="Blocked-scanner rules to summarise (default: 10)")
        parser.add_argument("--csv", dest="csv_path", help="Write the endpoint findings to this CSV file")
        parser.add_argument("--dump-json", help="Save raw query results here for offline re-analysis")
        parser.add_argument("--from-json", help="Re-analyse a previous --dump-json file instead of querying AWS")
        parser.add_argument("--check-path", help="Diagnose a single URI path instead of querying logs")

    def handle(self, *args, **options):
        self.options = options
        # Validate before any AWS call so a typo fails instantly rather than after a round trip.
        self.window = parse_duration(options["since"])
        try:
            if options["check_path"]:
                self._check_path(options["check_path"])
            else:
                self._analyze()
        except WafLogsError as exc:
            raise CommandError(str(exc)) from exc
        except aws_errors() as exc:
            raise CommandError(
                f"AWS request failed: {exc}\nCheck --profile/--region, and that your SSO session is current."
            ) from exc

    # ------------------------------------------------------------------ analysis

    def _analyze(self):
        rows, totals = self._load_rows()
        if not rows:
            self.stdout.write(self.style.SUCCESS("\nNo WAF rule matches in this window."))
            return

        if len(rows) >= self.options["limit"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nHit the --limit of {self.options['limit']} rows; low-traffic paths may be missing."
                )
            )

        deployed_patterns = None if self.options["no_drift"] else self._load_deployed_patterns(self.session)
        rows = [row for row in rows if row.hits >= self.options["min_hits"]]
        findings = classify(rows, deployed_patterns)
        # Endpoints collapse by route; noise stays per-URI so the attack summary can count paths.
        endpoints = deduplicate_endpoints([f for f in findings if f.is_endpoint])
        noise = [f for f in findings if not f.is_endpoint]

        self._report_endpoints(endpoints)
        self._report_noise(noise, totals)
        self._report_next_steps(endpoints)

        if self.options["csv_path"]:
            self._write_csv(endpoints)

    def _load_rows(self) -> tuple[list[LogRow], list[dict]]:
        if self.options["from_json"]:
            return self._rows_from_dump()
        return self._rows_from_aws()

    def _rows_from_dump(self) -> tuple[list[LogRow], list[dict]]:
        path = self.options["from_json"]
        with open(path) as fh:
            payload = json.load(fh)
        rows = [LogRow(**row) for row in payload["rows"]]
        self.stdout.write(f"Re-analysing {len(rows)} rows from {path}")
        return rows, payload.get("totals", [])

    def _rows_from_aws(self) -> tuple[list[LogRow], list[dict]]:
        log_group = self.options["log_group"] or find_log_group(self.session)
        end = datetime.now(tz=UTC)
        start = end - self.window

        self.stdout.write(f"Log group: {log_group}")
        self.stdout.write(f"Window:    {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC")
        self.stdout.write("Querying CloudWatch Logs Insights...")

        rows = fetch_events(self.session, log_group, start, end, limit=self.options["limit"])
        totals = fetch_totals(self.session, log_group, start, end)

        if self.options["dump_json"]:
            payload = {"rows": [row.as_dict() for row in rows], "totals": totals}
            with open(self.options["dump_json"], "w") as fh:
                json.dump(payload, fh, indent=2)
            self.stdout.write(f"Raw results saved to {self.options['dump_json']}")

        return rows, totals

    @cached_property
    def session(self):
        return get_session(self.options["profile"], self.options["region"])

    def _load_deployed_patterns(self, session):
        deployed = fetch_deployed_patterns(session)
        if not deployed:
            self.stdout.write(
                self.style.WARNING("No WAF regex pattern sets found — skipping the deployed-coverage check.")
            )
            return None
        compiled, errors = compile_deployed_patterns(deployed)
        for error in errors:
            self.stdout.write(self.style.WARNING(f"Could not compile deployed pattern {error}"))
        return compiled

    # ------------------------------------------------------------------ reporting

    def _report_endpoints(self, endpoints: list[Finding]):
        self._heading("LEGITIMATE ENDPOINTS MATCHED BY WAF RULES")
        if not endpoints:
            self.stdout.write("  None — every match was against a path that doesn't resolve to a view.")
            return

        total_hits = sum(f.row.hits for f in endpoints)
        self.stdout.write(f"  {len(endpoints)} endpoint/rule combinations, {total_hits:,} requests\n")

        by_fix: dict[Fix, list[Finding]] = defaultdict(list)
        for finding in endpoints:
            by_fix[finding.fix].append(finding)

        for fix in (Fix.ADD_DECORATOR, Fix.DEPLOY, Fix.INVESTIGATE, Fix.UNSUPPORTED, Fix.NOT_PATH_BASED):
            group = sorted(by_fix.get(fix, []), key=lambda f: f.row.hits, reverse=True)
            if not group:
                continue
            style = self.style.ERROR if fix is Fix.ADD_DECORATOR else self.style.WARNING
            self.stdout.write(style(f"  {fix.value}  ({len(group)})"))
            for finding in group:
                self._write_finding(finding)
            self.stdout.write("")

    def _write_finding(self, finding: Finding):
        row = finding.row
        self.stdout.write(
            f"    {row.hits:>7,}  {row.outcome:<11}  {row.rule:<28}  {row.method:<7}  {finding.route or row.uri}"
        )
        detail = f"             {finding.source or '?'}  {finding.view_name or ''}"
        if finding.uri_count > 1:
            detail += f"  ({finding.uri_count} URIs, e.g. {row.uri})"
        self.stdout.write(detail)
        if finding.fix in (Fix.ADD_DECORATOR, Fix.NOT_PATH_BASED):
            self.stdout.write(f"             -> {finding.remedy}")
        elif finding.fix is Fix.INVESTIGATE:
            self.stdout.write(f"             -> last seen {row.last_seen} — check this postdates the last deploy")
        elif finding.fix is Fix.UNSUPPORTED:
            self.stdout.write(f"             -> no WafRule for '{row.rule}'; needs a WAF rule change in ocs-deploy")

    def _report_noise(self, noise: list[Finding], totals: list[dict]):
        self._heading("WAF MATCHES THAT AREN'T A DJANGO ENDPOINT")
        if not noise:
            self.stdout.write("  None.")
            return

        by_rule: dict[str, list[Finding]] = defaultdict(list)
        for finding in noise:
            by_rule[finding.row.rule].append(finding)

        total_hits = sum(f.row.hits for f in noise)
        self.stdout.write(
            f"  {total_hits:,} requests across {len(noise):,} distinct paths — scanner and exploit traffic.\n"
        )

        ranked = sorted(by_rule.items(), key=lambda item: sum(f.row.hits for f in item[1]), reverse=True)
        for rule, group in ranked[: self.options["attacks"]]:
            self._write_rule_summary(rule, group)

        if len(ranked) > self.options["attacks"]:
            self.stdout.write(f"    ... and {len(ranked) - self.options['attacks']} more rules (raise --attacks)")

        capped = [t for t in totals if t["unique_paths"] >= self.options["limit"]]
        if capped:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  Note: {len(capped)} rule(s) saw more distinct paths than --limit allowed us to fetch."
                )
            )

    def _write_rule_summary(self, rule: str, group: list[Finding]):
        hits = sum(f.row.hits for f in group)
        # Per-URI rows can't be summed into a distinct-IP count, so report the widest single path.
        ips = max((f.row.unique_ips for f in group), default=0)
        countries = max((f.row.unique_countries for f in group), default=0)
        self.stdout.write(f"    {hits:>7,}  {rule:<28}  {len(group):>5} paths  {ips:>4} IPs  {countries:>3} countries")
        for finding in sorted(group, key=lambda f: f.row.hits, reverse=True)[:3]:
            self.stdout.write(f"             {finding.row.uri[:100]}")

    def _report_next_steps(self, endpoints: list[Finding]):
        needs_decorator = [f for f in endpoints if f.fix is Fix.ADD_DECORATOR]
        needs_deploy = [f for f in endpoints if f.fix is Fix.DEPLOY]
        if not needs_decorator and not needs_deploy:
            return

        self._heading("NEXT STEPS")
        step = 1
        if needs_decorator:
            self.stdout.write(f"  {step}. Add the decorators listed above (must be the topmost decorator).")
            step += 1
        if needs_deploy:
            self.stdout.write(f"  {step}. {len(needs_deploy)} endpoint(s) are decorated but not live in the WAF.")
            step += 1
        self.stdout.write(f"  {step}. ./manage.py export_waf_allow_list")
        self.stdout.write(f"  {step + 1}. Copy the output into ocs_deploy/waf_utils.py and deploy the waf stack.")

    def _heading(self, title):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(title))
        self.stdout.write("=" * len(title))

    def _write_csv(self, endpoints: list[Finding]):
        with open(self.options["csv_path"], "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for finding in sorted(endpoints, key=lambda f: f.row.hits, reverse=True):
                writer.writerow(finding.as_dict())
        self.stdout.write(f"\nEndpoint findings written to {self.options['csv_path']}")

    # ------------------------------------------------------------------ single path

    def _check_path(self, path):
        deployed_patterns = None
        if not self.options["no_drift"]:
            deployed_patterns = self._load_deployed_patterns(self.session)

        self.stdout.write(f"\nPath: {path}")
        findings = [classify_row(_blank_row(path, rule.name), deployed_patterns) for rule in WafRule]
        if not findings[0].is_endpoint:
            self.stdout.write(self.style.WARNING("  Does not resolve to any Django view."))
            return

        self.stdout.write(f"  Route:  {findings[0].route}")
        self.stdout.write(f"  View:   {findings[0].view_name}  ({findings[0].source})")
        for finding in findings:
            deployed = {True: "yes", False: "no", None: "not checked"}[finding.deployed]
            decorated = "yes" if finding.decorated else "no"
            self.stdout.write(f"  {finding.row.rule:<24} decorated: {decorated:<5} deployed: {deployed}")


def _blank_row(uri: str, rule: str) -> LogRow:
    return LogRow(
        uri=uri,
        method="",
        rule=rule,
        action="",
        effective_action="",
        hits=0,
        unique_ips=0,
        unique_countries=0,
        first_seen="",
        last_seen="",
    )


def parse_duration(value: str) -> timedelta:
    match = DURATION_RE.match(value.strip())
    if not match:
        raise CommandError(f"Invalid --since value {value!r}. Use forms like 90m, 24h or 7d.")
    amount, unit = match.groups()
    return timedelta(seconds=int(amount) * UNIT_SECONDS[unit])
