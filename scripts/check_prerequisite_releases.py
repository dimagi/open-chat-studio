#!/usr/bin/env python3
"""Fail a pull request until every PR it lists as a prerequisite is in a tagged release.

Driven by `.github/workflows/pr_prerequisites.yml`. Authors list prerequisites in the
"Prerequisite PRs" section of the PR description, one per line as ``- #1234``. These are
merged PRs whose manual steps (for example a data migration command) must have run on a
deployment before this PR can ship. A missing section, an empty one, or one that says
"None" means there are no prerequisites.

A prerequisite counts as released once its merge commit is reachable from a ``v*`` tag,
pre-release tags such as ``v1.0.0-rc1`` included. Widget ``w_v*`` tags do not count.
Needs a clone with full history and tags, since the check is ``git tag --contains``.

    python3 scripts/check_prerequisite_releases.py --repo dimagi/open-chat-studio --pr 1234
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

RELEASE_TAG_PATTERN = "v*"

SECTION_HEADING = re.compile(r"^#{1,6}\s*Prerequisite PRs\s*:?\s*$", re.IGNORECASE)
# A heading or a horizontal rule ends the section; this repo's PR bodies end with `---` and a footer.
SECTION_END = re.compile(r"^(?:#{1,6}\s|---+$)")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PREREQUISITE_LINE = re.compile(r"^(?:[-*+]\s*)?#(\d+)\b")
PR_NUMBER = re.compile(r"#\d+")
NONE_LINE = re.compile(r"^(?:[-*+]\s*)?none\.?$", re.IGNORECASE)


class UnreadableSection(ValueError):
    pass


@dataclass(frozen=True)
class Prerequisite:
    number: int
    merge_commit_sha: str | None
    """None when the PR is not merged."""


def parse_prerequisites(body: str | None) -> list[int]:
    """PR numbers listed in the "Prerequisite PRs" section, in order and without duplicates."""
    lines = HTML_COMMENT.sub("", body or "").splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if SECTION_HEADING.match(line.strip())), None)
    if start is None:
        return []

    numbers: list[int] = []
    unreadable: list[str] = []
    for line in lines[start:]:
        line = line.strip()
        if SECTION_END.match(line):
            break
        if not line or NONE_LINE.match(line):
            continue
        match = PREREQUISITE_LINE.match(line)
        if match and len(PR_NUMBER.findall(line)) == 1:
            number = int(match.group(1))
            if number not in numbers:
                numbers.append(number)
        else:
            unreadable.append(line)

    if unreadable:
        quoted = ", ".join(repr(line) for line in unreadable)
        raise UnreadableSection(
            f"The Prerequisite PRs section has lines that are not one PR in the form `- #1234`: {quoted}"
        )
    return numbers


def release_tags_containing(sha: str) -> list[str] | None:
    """Release tags whose history includes the commit, or None if the commit is not in this clone."""
    result = subprocess.run(
        ["git", "tag", "--list", RELEASE_TAG_PATTERN, "--contains", sha],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.split()


def find_blockers(prerequisites: list[Prerequisite]) -> list[str]:
    """A message for each prerequisite that is not merged or not yet in a release."""
    blockers = []
    for prerequisite in prerequisites:
        sha = prerequisite.merge_commit_sha
        if sha is None:
            blockers.append(f"#{prerequisite.number} is not merged")
            continue
        tags = release_tags_containing(sha)
        if tags is None:
            blockers.append(f"#{prerequisite.number}: its merge commit {sha[:12]} is not in this clone")
        elif not tags:
            blockers.append(f"#{prerequisite.number} is merged but not yet in a release")
    return blockers


def gh_api(path: str) -> dict:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"`gh api {path}` exited {result.returncode}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_prerequisite(repo: str, number: int) -> Prerequisite:
    pull = gh_api(f"repos/{repo}/pulls/{number}")
    return Prerequisite(number=number, merge_commit_sha=pull["merge_commit_sha"] if pull.get("merged") else None)


def check(repo: str, pr: int) -> list[str]:
    """Every reason the PR may not merge yet. The body is fetched live, so a re-run sees edits."""
    try:
        numbers = parse_prerequisites(gh_api(f"repos/{repo}/pulls/{pr}").get("body"))
    except UnreadableSection as error:
        return [str(error)]

    prerequisites = []
    blockers = []
    for number in numbers:
        try:
            prerequisites.append(fetch_prerequisite(repo=repo, number=number))
        except RuntimeError as error:
            blockers.append(f"#{number} could not be read as a pull request in {repo}: {error}")
    return blockers + find_blockers(prerequisites)


def write_summary(text: str) -> None:
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as summary:
            summary.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    args = parser.parse_args(argv)

    blockers = check(repo=args.repo, pr=args.pr)
    if not blockers:
        message = "Every prerequisite PR is in a release."
        print(message)
        write_summary(message)
        return 0

    for blocker in blockers:
        print(f"::error::{blocker}")
    write_summary("## Waiting on prerequisite PRs\n\n" + "\n".join(f"- {blocker}" for blocker in blockers))
    return 1


if __name__ == "__main__":
    sys.exit(main())
