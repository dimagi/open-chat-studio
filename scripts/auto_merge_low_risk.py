#!/usr/bin/env python3
"""Merge `risk:low` pull requests that have passed everything, or report what would merge.

Driven by `.github/workflows/auto_merge_low_risk.yml`. Reports only unless
``--execute`` is passed, so it can run for a while in dry run and be read back
against what humans actually merged.

Merging to `main` deploys to production, so every condition here is a refusal by
default: a check that has not reported, a verdict that is missing, a label that is
absent all block the merge.

**The `risk:low` label is not trusted.** It is only a cheap filter for which pull
requests to look at; the gate in `pr_risk_gate.py` is then re-run here, from this
checkout of the default branch, against the pull request's live file list. Trusting
the label would mean trusting two things that a pull request author controls: the
label itself, which anyone with write access can apply by hand, and
`pr_risk_label.yml`, which GitHub runs *from the pull request's head ref* -- so a
pull request could rewrite the labeller to call itself low while keeping the job
name its own check is looked up by. Re-running the gate here closes both, and a
pull request that edits `.github/**` classifies as high on its own rules anyway.

The merge is pinned to the commit that was judged, so a push landing mid-run aborts
it rather than merging unjudged code.

    python3 scripts/auto_merge_low_risk.py --repo dimagi/open-chat-studio
    python3 scripts/auto_merge_low_risk.py --repo dimagi/open-chat-studio --execute
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

import pr_risk_gate

REQUIRED_LABEL = "risk:low"
BLOCKING_LABELS = frozenset({"wip"})
BASE_BRANCH = "main"

# `Classify risk` shows the labeller ran on this exact commit; the verdict is the code
# review. Neither is load-bearing for the risk decision -- that is recomputed below.
REQUIRED_CHECKS = ("Classify risk", "Automated review verdict")


# A review that only comments leaves the reviewer's standing position unchanged.
IGNORED_REVIEW_STATES = ("COMMENTED", "PENDING")

# A check that deliberately did not apply to this diff is not a failure.
ACCEPTABLE_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

# `clean` only. `unstable` also covers a red or pending legacy *commit status*, which
# external integrations post and the Check Runs API below cannot see -- accepting it
# would let an integration's red signal through the moment one is installed.
MERGEABLE_STATES = frozenset({"clean"})


def latest_check_runs(check_runs: list[dict]) -> dict[str, dict]:
    """The most recent run per check name, since a re-run leaves the old one in place."""
    latest: dict[str, dict] = {}
    # `id` breaks ties: two runs of the same check can share a `started_at` to the second,
    # and the list order the API returns them in is not chronological.
    for run in sorted(check_runs, key=lambda run: (run.get("started_at") or "", run.get("id") or 0)):
        latest[run["name"]] = run
    return latest


def latest_review_states(reviews: list[dict]) -> dict[str, str]:
    """Each reviewer's standing position. A plain comment leaves their position unchanged."""
    states: dict[str, str] = {}
    for review in reviews:
        state = review.get("state")
        if not state or state in IGNORED_REVIEW_STATES:
            continue
        login = (review.get("user") or {}).get("login")
        if login:
            states[login] = state
    return states


def pull_blockers(pull: dict, repo: str, recomputed_risk: str) -> list[str]:
    """Blockers that follow from the pull request itself, before any check is read."""
    blockers = []
    labels = {label["name"] for label in pull.get("labels", [])}

    if pull.get("draft"):
        blockers.append("it is a draft")
    if REQUIRED_LABEL not in labels:
        blockers.append(f"it is not labelled {REQUIRED_LABEL}")
    blockers.extend(f"it carries the {label} label" for label in sorted(BLOCKING_LABELS & labels))
    if pull["base"]["ref"] != BASE_BRANCH:
        blockers.append(f"it targets {pull['base']['ref']}, not {BASE_BRANCH}")
    if ((pull["head"].get("repo") or {}).get("full_name")) != repo:
        blockers.append("the head branch is on a fork")
    if pull.get("mergeable") is not True:
        blockers.append(f"GitHub reports mergeable={pull.get('mergeable')}")
    if pull.get("mergeable_state") not in MERGEABLE_STATES:
        blockers.append(f"its mergeable_state is {pull.get('mergeable_state')}")
    if recomputed_risk != pr_risk_gate.LOW:
        blockers.append(f"the gate re-runs this as {recomputed_risk}, whatever the label says")
    return blockers


def check_blockers(check_runs: list[dict]) -> list[str]:
    blockers = []
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
    return blockers


def review_blockers(reviews: list[dict]) -> list[str]:
    return [
        f"{login} requested changes"
        for login, state in sorted(latest_review_states(reviews).items())
        if state == "CHANGES_REQUESTED"
    ]


def find_blockers(
    pull: dict, check_runs: list[dict], reviews: list[dict], repo: str, *, recomputed_risk: str
) -> list[str]:
    """Every reason this pull request may not be merged unattended."""
    return [
        *pull_blockers(pull, repo, recomputed_risk),
        *check_blockers(check_runs),
        *review_blockers(reviews),
    ]


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"`gh {' '.join(args)}` exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def gh_api(*args: str) -> Any:
    return json.loads(gh("api", *args))


def gh_paginated(path: str) -> list[dict]:
    pages: list[list[dict]] = gh_api(path, "--paginate", "--slurp")
    return [entry for page in pages for entry in page]


def check_runs_for(repo: str, sha: str) -> list[dict]:
    """Every check run on a commit. A busy commit with re-runs can exceed one page."""
    pages: list[dict] = gh_api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", "--paginate", "--slurp")
    return [run for page in pages for run in page["check_runs"]]


def recompute_risk(repo: str, number: int, head_repo: str | None) -> str:
    """Re-run the gate here rather than believing the label or the check that applied it.

    Trust is "the branch lives in this repo", which takes write access to create. Not
    `author_association`, which reads CONTRIBUTOR for anyone whose org membership is
    private and would quietly withhold `risk:low` from most of the team.
    """
    files = gh_paginated(f"repos/{repo}/pulls/{number}/files?per_page=100")
    return pr_risk_gate.classify(files, untrusted=head_repo != repo).risk


def judge(repo: str, number: int) -> tuple[dict, list[str]]:
    """Read everything this pull request is judged on, and return it with its blockers."""
    # The list payload omits mergeable/mergeable_state; only the single-PR read has them.
    pull: dict = gh_api(f"repos/{repo}/pulls/{number}")
    check_runs = check_runs_for(repo, pull["head"]["sha"])
    reviews = gh_paginated(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    risk = recompute_risk(repo, number, (pull["head"].get("repo") or {}).get("full_name"))
    return pull, find_blockers(pull, check_runs, reviews, repo, recomputed_risk=risk)


def open_low_risk_pulls(repo: str) -> list[dict]:
    pulls = gh_paginated(f"repos/{repo}/pulls?state=open&per_page=100")
    return [pull for pull in pulls if any(label["name"] == REQUIRED_LABEL for label in pull.get("labels", []))]


def merge(repo: str, number: int, method: str, head_sha: str) -> None:
    """Merge, refusing if the head moved since it was judged -- GitHub 409s on a stale `sha`."""
    gh(
        "api",
        "-X",
        "PUT",
        f"repos/{repo}/pulls/{number}/merge",
        "-f",
        f"merge_method={method}",
        "-f",
        f"sha={head_sha}",
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
    failed = 0
    for candidate in candidates:
        number = candidate["number"]
        lines.append(f"### #{number} {candidate['title']}")
        try:
            pull, blockers = judge(args.repo, number)
        except RuntimeError as exc:
            # One unreachable candidate must not cost the run its summary or the rest of the queue.
            lines.extend([f"Held back, could not be read: {exc}", ""])
            continue

        if blockers:
            lines.append("Held back because:")
            lines.extend([*(f"- {blocker}" for blocker in blockers), ""])
            continue
        if not args.execute:
            lines.extend(["**Would merge.**", ""])
            continue

        try:
            merge(args.repo, number, args.merge_method, pull["head"]["sha"])
        except RuntimeError as exc:
            failed += 1
            lines.extend([f"Merge failed: {exc}", ""])
            continue

        merged += 1
        lines.extend(
            [
                "Merged.",
                "",
                "Stopping here: `main` has moved, so every candidate behind this one was judged "
                "against a base that no longer exists. The next poll re-reads them.",
            ]
        )
        break

    if args.execute:
        lines.append(f"Merged {merged} of {len(candidates)} candidate(s).")
        if failed:
            lines.append(f"{failed} merge(s) failed.")
    report(lines)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
