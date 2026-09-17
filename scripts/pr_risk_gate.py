#!/usr/bin/env python3
"""Classify a pull request as risk:low, risk:medium or risk:high from its file list.

Deterministic gate behind the PR risk label (`.github/workflows/pr_risk_label.yml`).
Merging to `main` deploys to production, so `risk:low` is the gate for unattended
auto-merge: an allowlist of change shapes that cannot alter running behaviour,
not a judgement about how small a diff looks.

The decision is evaluated in order:

1. Any blocker -- a blocked path, a non-docs deletion, or a diff that loses or
   disables test coverage -- wins outright and yields ``high``.
2. Every file on the low allowlist, within the size caps, yields ``low``.
3. Everything else yields ``medium``.

``--untrusted`` withholds ``low`` from forks and authors without write access:
an outsider's docs typo is not high risk, but it is not eligible for unattended
merge either. A blocker still reports ``high``.

Reads the GitHub "list pull request files" payload, one array per page:

    gh api "repos/$REPO/pulls/$N/files?per_page=100" --paginate --slurp > files.json
    python3 scripts/pr_risk_gate.py files.json
"""

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

HIGH = "risk:high"
MEDIUM = "risk:medium"
LOW = "risk:low"

# A low-risk PR has to be reviewable at a glance if anyone ever does look at it.
MAX_FILES = 25
MAX_CHANGED_LINES = 500

# Paths where a change can alter production behaviour, the deploy, the security
# boundary, or a contract someone else depends on. Order is irrelevant; the first
# match reports.
BLOCKED_PATHS: list[tuple[str, str]] = [
    ("apps/*/migrations/**", "schema migration"),
    ("apps/data_migrations/**", "data migration"),
    ("config/**", "deploy-wide Django configuration"),
    (".github/**", "CI/CD definition"),
    ("Dockerfile*", "container image"),
    ("docker-compose*", "container image"),
    ("pyproject.toml", "dependency manifest"),
    ("uv.lock", "dependency lockfile"),
    ("package.json", "dependency manifest"),
    ("pnpm-lock.yaml", "dependency lockfile"),
    (".pre-commit-config.yaml", "commit-time checks"),
    ("apps/teams/**", "team membership and the multi-tenancy boundary"),
    ("apps/users/**", "user accounts"),
    ("apps/sso/**", "authentication"),
    ("apps/oauth/**", "authentication"),
    ("apps/api/**", "public API contract"),
    ("apps/service_providers/**", "service credentials"),
    ("apps/cost_tracking/**", "billing"),
    ("apps/usage_metrics/**", "billing"),
    ("**/urls.py", "URL routing surface"),
    ("**/CLAUDE.md", "instructions the automated reviewer reads"),
    ("**/AGENTS.md", "instructions the automated reviewer reads"),
    (".claude/**", "agent configuration the automated reviewer reads"),
    (".mcp.json", "agent configuration the automated reviewer reads"),
    ("scripts/**", "developer tooling and the checks that gate CI"),
    ("docs/adr/**", "accepted architecture decision record"),
    ("api-schemas/**", "generated API schema"),
]

# Change shapes that cannot reach running code.
LOW_RISK_PATHS: list[tuple[str, str]] = [
    ("docs/**", "documentation"),
    ("**/*.md", "documentation"),
    ("mkdocs.yml", "documentation"),
    ("apps/*/tests/**", "tests"),
    ("apps/*/tests.py", "tests"),
    ("apps/**/test_*.py", "tests"),
]

DOCS_PATHS = [pattern for pattern, reason in LOW_RISK_PATHS if reason == "documentation"]
TEST_PATHS = [pattern for pattern, reason in LOW_RISK_PATHS if reason == "tests"]

# Adding an ADR is ordinary; editing or deleting an accepted one is the "ask first" rule.
BLOCKED_ONLY_WHEN_MODIFIED = ("docs/adr/**",)

TEST_DEF = re.compile(r"^([-+])\s*(?:async\s+)?def test_")
DISABLED_TEST = re.compile(r"^\+.*(?:pytest\.mark\.(?:skip|xfail)|pytest\.skip\()")


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a glob where ``**`` spans directory separators into a regex."""
    parts = pattern.split("/")
    out = ["^"]
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".+" if last else "(?:[^/]+/)*")
        else:
            segment = re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            out.append(segment if last else segment + "/")
    out.append("$")
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    return bool(_compile(pattern).match(path))


def match_category(path: str, table: list[tuple[str, str]]) -> str | None:
    for pattern, reason in table:
        if glob_match(path, pattern):
            return reason
    return None


def is_docs(path: str) -> bool:
    return any(glob_match(path, pattern) for pattern in DOCS_PATHS)


def is_test(path: str) -> bool:
    return any(glob_match(path, pattern) for pattern in TEST_PATHS)


@dataclass
class Verdict:
    risk: str
    blockers: list[str] = field(default_factory=list)
    disqualifiers: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return self.blockers or self.disqualifiers

    def as_dict(self) -> dict:
        return {"risk": self.risk, "reasons": self.reasons}


def _paths_of(entry: dict) -> list[str]:
    """Every path a change touches -- a rename exposes the destination and the source."""
    paths = [entry["filename"]]
    previous = entry.get("previous_filename")
    if previous:
        paths.append(previous)
    return paths


def count_lost_tests(files: list[dict]) -> int:
    """Net test functions the diff drops. Renaming or re-signaturing a test nets to zero.

    Test files only: `docs/` carries plan documents with `def test_` samples in them, and
    production code carries methods that merely start with `test_`. Neither is coverage.
    """
    added = removed = 0
    for entry in files:
        if not is_test(entry["filename"]):
            continue
        for line in (entry.get("patch") or "").splitlines():
            match = TEST_DEF.match(line)
            if not match:
                continue
            if match.group(1) == "+":
                added += 1
            else:
                removed += 1
    return max(removed - added, 0)


def path_blockers(entry: dict) -> list[str]:
    """Blockers that follow from where a file sits, and from the file appearing or leaving."""
    status = entry.get("status", "modified")
    blockers = []
    for path in _paths_of(entry):
        # A test file under a blocked app is still only ever imported by pytest, so the
        # path blockers do not apply to it. Losing or disabling one below still counts.
        # Confined to `apps/` -- outside it a `test_*.py` name proves nothing about what
        # loads the file, and `.github/` and `config/` would hand out the exemption on a name.
        if path.startswith("apps/") and is_test(path):
            continue
        if status == "added" and any(glob_match(path, pattern) for pattern in BLOCKED_ONLY_WHEN_MODIFIED):
            continue
        reason = match_category(path, BLOCKED_PATHS)
        if reason:
            blockers.append(f"{path}: {reason}")
    if status in ("removed", "renamed") and not all(is_docs(path) for path in _paths_of(entry)):
        blockers.append(f"{entry['filename']}: file {status}")
    return blockers


def disables_a_test(entry: dict) -> bool:
    if not entry["filename"].endswith(".py"):
        return False
    return any(DISABLED_TEST.match(line) for line in (entry.get("patch") or "").splitlines())


def find_blockers(files: list[dict]) -> list[str]:
    blockers = []
    for entry in files:
        blockers.extend(path_blockers(entry))
        if disables_a_test(entry):
            blockers.append(f"{entry['filename']}: disables a test")

    lost = count_lost_tests(files)
    if lost:
        blockers.append(f"the diff removes {lost} more test(s) than it adds")
    return blockers


def find_disqualifiers(files: list[dict]) -> list[str]:
    """Reasons the PR cannot be proven low, short of being a blocker."""
    disqualifiers = []
    if len(files) > MAX_FILES:
        disqualifiers.append(f"touches {len(files)} files (cap is {MAX_FILES})")
    changed_lines = sum(entry.get("additions", 0) + entry.get("deletions", 0) for entry in files)
    if changed_lines > MAX_CHANGED_LINES:
        disqualifiers.append(f"changes {changed_lines} lines (cap is {MAX_CHANGED_LINES})")
    for entry in files:
        path = entry["filename"]
        if not match_category(path, LOW_RISK_PATHS):
            disqualifiers.append(f"{path}: not on the low-risk allowlist")
        elif entry.get("patch") is None and entry.get("status") != "removed":
            # No patch means the content blockers could not be evaluated for this file.
            disqualifiers.append(f"{path}: diff too large to inspect")
    return disqualifiers


def classify(files: list[dict], *, untrusted: bool = False) -> Verdict:
    if not files:
        return Verdict(MEDIUM, disqualifiers=["no files reported for this pull request"])

    blockers = find_blockers(files)
    if blockers:
        return Verdict(HIGH, blockers=blockers)

    disqualifiers = find_disqualifiers(files)
    if untrusted:
        disqualifiers.append("author is a fork contributor or lacks write access")
    if disqualifiers:
        return Verdict(MEDIUM, disqualifiers=disqualifiers)
    return Verdict(LOW)


def load_files(source: str) -> list[dict]:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text()
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("expected a JSON array of pull request files")
    # `gh api --paginate --slurp` yields one array per page.
    if payload and isinstance(payload[0], list):
        return [entry for page in payload for entry in page]
    return payload


def write_github_output(verdict: Verdict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    # A file name can contain newlines, and reasons quote file names. A fixed delimiter
    # would let one close the heredoc and append output records of its own -- including a
    # second `risk=` that wins by being last.
    delimiter = f"REASONS_EOF_{uuid.uuid4().hex}"
    with open(path, "a") as fh:
        fh.write(f"risk={verdict.risk}\n")
        fh.write(f"reasons<<{delimiter}\n")
        for reason in verdict.reasons:
            fh.write("- " + " ".join(reason.splitlines()) + "\n")
        fh.write(f"{delimiter}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files_json", help="Path to the pull request file list, or - for stdin")
    parser.add_argument(
        "--untrusted",
        action="store_true",
        help="Cap the result at risk:medium (fork PRs, authors without write access)",
    )
    args = parser.parse_args(argv)

    verdict = classify(load_files(args.files_json), untrusted=args.untrusted)
    write_github_output(verdict)
    print(json.dumps(verdict.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
