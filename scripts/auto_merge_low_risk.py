#!/usr/bin/env python3
"""Merge `risk:low` pull requests that have passed everything, or report what would merge.

Driven by `.github/workflows/auto_merge_low_risk.yml`. Reports only unless
``--execute`` is passed, so it can run for a while in dry run and be read back
against what humans actually merged.

Merging to `main` deploys to production, so every condition here is a refusal by
default: a check that has not reported, a verdict that is missing, a label that
is absent or stale all block the merge. `risk:low` alone is never enough --
`scripts/pr_risk_gate.py` decides the label, and this decides whether the rest of
the pull request agrees.

The required checks are demanded by name on the head commit, which is what makes
the label trustworthy: `Classify risk` only succeeds on the commit it classified,
so a push after the label was applied cannot inherit it.

    python3 scripts/auto_merge_low_risk.py --repo dimagi/open-chat-studio
    python3 scripts/auto_merge_low_risk.py --repo dimagi/open-chat-studio --execute
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

REQUIRED_LABEL = "risk:low"
BLOCKING_LABELS = frozenset({"wip"})
BASE_BRANCH = "main"

# `Classify risk` ties the label to this exact commit; the verdict is the code review.
REQUIRED_CHECKS = ("Classify risk", "Automated review verdict")

# A check that deliberately did not apply to this diff is not a failure.
ACCEPTABLE_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

# `unstable` means a non-required check is red -- every check is judged below, so
# GitHub's own idea of which ones are required does not get a say.
MERGEABLE_STATES = frozenset({"clean", "unstable"})


def latest_check_runs(check_runs: list[dict]) -> dict[str, dict]:
    """The most recent run per check name, since a re-run leaves the old one in place."""
    latest: dict[str, dict] = {}
    for run in sorted(check_runs, key=lambda run: run.get("started_at") or ""):
        latest[run["name"]] = run
    return latest


def latest_review_states(reviews: list[dict]) -> dict[str, str]:
    """Each reviewer's standing position. A plain comment leaves their position unchanged."""
    states: dict[str, str] = {}
    for review in reviews:
        state = review.get("state")
        login = (review.get("user") or {}).get("login")
        if not state or not login or state in ("COMMENTED", "PENDING"):
            continue
        states[login] = state
    return states


def find_blockers(pull: dict, check_runs: list[dict], reviews: list[dict], repo: str) -> list[str]:
    """Every reason this pull request may not be merged unattended."""
    blockers = []
    labels = {label["name"] for label in pull.get("labels", [])}

    if pull.get("draft"):
        blockers.append("it is a draft")
    if REQUIRED_LABEL not in labels:
        blockers.append(f"it is not labelled {REQUIRED_LABEL}")
    for label in sorted(BLOCKING_LABELS & labels):
        blockers.append(f"it carries the {label} label")
    if pull["base"]["ref"] != BASE_BRANCH:
        blockers.append(f"it targets {pull['base']['ref']}, not {BASE_BRANCH}")
    if ((pull["head"].get("repo") or {}).get("full_name")) != repo:
        blockers.append("the head branch is on a fork")
    if pull.get("mergeable") is not True:
        blockers.append(f"GitHub reports mergeable={pull.get('mergeable')}")
    if pull.get("mergeable_state") not in MERGEABLE_STATES:
        blockers.append(f"its mergeable_state is {pull.get('mergeable_state')}")

    latest = latest_check_runs(check_runs)
    for name in REQUIRED_CHECKS:
        run = latest.get(name)
        if run is None:
            blockers.append(f"the required check {name!r} has not reported on this commit")
        elif run.get("conclusion") != "success":
            blockers.append(f"the required check {name!r} is {run.get('conclusion') or run.get('status')}")
    for name, run in sorted(latest.items()):
        if name in REQUIRED_CHECKS:
            continue
        if run.get("status") != "completed":
            blockers.append(f"the check {name!r} is still {run.get('status')}")
        elif run.get("conclusion") not in ACCEPTABLE_CONCLUSIONS:
            blockers.append(f"the check {name!r} concluded {run.get('conclusion')}")

    for login, state in sorted(latest_review_states(reviews).items()):
        if state == "CHANGES_REQUESTED":
            blockers.append(f"{login} requested changes")

    return blockers


def gh_api(*args: str) -> Any:
    result = subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def gh_paginated(path: str) -> list[dict]:
    pages: list[list[dict]] = gh_api(path, "--paginate", "--slurp")
    return [entry for page in pages for entry in page]


def open_low_risk_pulls(repo: str) -> list[dict]:
    pulls = gh_paginated(f"repos/{repo}/pulls?state=open&per_page=100")
    return [pull for pull in pulls if any(label["name"] == REQUIRED_LABEL for label in pull.get("labels", []))]


def merge(repo: str, number: int, method: str) -> None:
    subprocess.run(
        ["gh", "api", "-X", "PUT", f"repos/{repo}/pulls/{number}/merge", "-f", f"merge_method={method}"],
        check=True,
    )


def report(lines: list[str]) -> None:
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/name")
    parser.add_argument("--execute", action="store_true", help="Actually merge. Without it, only report.")
    parser.add_argument("--merge-method", default="merge", choices=["merge", "squash", "rebase"])
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo is required when GITHUB_REPOSITORY is unset")

    mode = "Merging" if args.execute else "Dry run — reporting only"
    lines = [f"## Auto-merge: {mode}", ""]

    candidates = open_low_risk_pulls(args.repo)
    if not candidates:
        lines.append(f"No open pull requests are labelled `{REQUIRED_LABEL}`.")
        report(lines)
        return 0

    merged = 0
    for candidate in candidates:
        number = candidate["number"]
        # The list payload omits mergeable/mergeable_state; only the single-PR read has them.
        pull: dict = gh_api(f"repos/{args.repo}/pulls/{number}")
        head_sha = pull["head"]["sha"]
        check_runs = gh_api(f"repos/{args.repo}/commits/{head_sha}/check-runs?per_page=100")["check_runs"]
        reviews = gh_paginated(f"repos/{args.repo}/pulls/{number}/reviews?per_page=100")

        blockers = find_blockers(pull, check_runs, reviews, args.repo)
        lines.append(f"### #{number} {pull['title']}")
        if blockers:
            lines.append("Held back because:")
            lines.extend(f"- {blocker}" for blocker in blockers)
        elif args.execute:
            merge(args.repo, number, args.merge_method)
            merged += 1
            lines.append("Merged.")
        else:
            lines.append("**Would merge.**")
        lines.append("")

    if args.execute:
        lines.append(f"Merged {merged} of {len(candidates)} candidate(s).")
    report(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
