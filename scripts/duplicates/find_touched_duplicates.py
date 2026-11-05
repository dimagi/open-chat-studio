#!/usr/bin/env python3
"""
Report duplicate code that overlaps lines changed on the current branch.

Runs treepeat over the given paths, then keeps only the duplicate groups where
at least one region overlaps a line the branch touched. A group counts whether
the branch added the duplicate or edited code that was already duplicated, so
the report covers both new duplication and existing duplication worth cleaning
up while you are there.

Touched lines come from diffing the merge base with the base branch against
the working tree, so uncommitted edits and untracked files count. The base
branch is ``--base`` if given, else the pull request's target branch from
``GITHUB_BASE_REF`` in GitHub Actions, else ``origin/main``. Deleting lines
counts as touching the lines either side of the deletion.

The report is advisory: the exit code is 0 unless ``--fail`` is passed. It is 2
when git or treepeat cannot run.

Usage:
    uv run inv duplicates
    uv run python -m scripts.duplicates.find_touched_duplicates --base origin/main --format json apps
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
WHOLE_FILE = (1, sys.maxsize)

type TouchedLines = dict[str, list[tuple[int, int]]]


@dataclass(frozen=True)
class Region:
    path: str
    start: int
    end: int
    name: str = ""
    kind: str = ""

    def overlaps(self, ranges: list[tuple[int, int]]) -> bool:
        return any(start <= self.end and self.start <= end for start, end in ranges)


@dataclass(frozen=True)
class Group:
    similarity: float
    regions: list[Region]


@dataclass(frozen=True)
class TouchedGroup:
    group: Group
    touched: list[Region]
    others: list[Region]


def parse_diff(diff: str) -> TouchedLines:
    """Map each file in a ``git diff -U0`` to the line ranges it touches in the new version."""
    touched: TouchedLines = {}
    path = None
    # File headers come between "diff --git" and the first hunk; later "+++ " lines are added content.
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_header = True
        elif in_header and line.startswith("+++ "):
            path = _new_path(line)
        elif match := HUNK_HEADER.match(line):
            in_header = False
            if path:
                touched.setdefault(path, []).append(_hunk_range(match))
    return touched


def _new_path(header: str) -> str | None:
    """Return the path from a ``+++ b/<path>`` header, or None for a deleted file (``+++ /dev/null``)."""
    # Git ends the header with a tab when the path contains a space.
    return header.removeprefix("+++ b/").removesuffix("\t") if header.startswith("+++ b/") else None


def _hunk_range(match: re.Match) -> tuple[int, int]:
    """Return the line range a hunk header touches in the new version."""
    start = int(match["start"])
    count = int(match["count"]) if match["count"] is not None else 1
    if count:
        return (start, start + count - 1)
    # A pure deletion is reported as the line before the removed lines.
    return (start, start + 1) if start else (1, 1)


def resolve_base(base: str | None) -> str:
    """Return ``base``, else the pull request's target branch in GitHub Actions, else ``origin/main``."""
    if base:
        return base
    if github_base_ref := os.environ.get("GITHUB_BASE_REF"):
        return f"origin/{github_base_ref}"
    return "origin/main"


def get_touched_lines(base: str, repo_root: Path) -> TouchedLines:
    """Return the line ranges changed since the merge base with ``base``, including uncommitted work."""
    merge_base = _git(["merge-base", base, "HEAD"], repo_root=repo_root).strip()
    diff = _git(
        ["diff", "-U0", "-M", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", merge_base],
        repo_root=repo_root,
    )
    touched = parse_diff(diff)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], repo_root=repo_root)
    for path in untracked.splitlines():
        touched[path] = [WHOLE_FILE]
    return touched


def _git(args: list[str], repo_root: Path) -> str:
    # core.quotePath=false stops git quoting non-ASCII paths, so they match treepeat's paths.
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout


def find_treepeat() -> str | None:
    """Return the path to the treepeat executable, or None if it is not installed."""
    treepeat = shutil.which("treepeat") or str(Path(sys.executable).parent / "treepeat")
    return treepeat if Path(treepeat).exists() else None


def run_treepeat(path: str, similarity: int, min_lines: int, repo_root: Path) -> dict:
    """Run treepeat and return its SARIF output as a dict."""
    treepeat = find_treepeat()
    if treepeat is None:
        raise FileNotFoundError("treepeat is not installed. Run `uv sync --group code-analysis` to install it.")
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "treepeat.sarif"
        subprocess.run(
            [treepeat, "detect", path, "-s", str(similarity), "-ml", str(min_lines), "-f", "sarif", "-o", output],
            cwd=repo_root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return json.loads(output.read_text())


def parse_treepeat_sarif(data: dict) -> list[Group]:
    """Read duplicate groups from treepeat's SARIF output."""
    return [
        Group(
            similarity=result["properties"]["similarityPercent"],
            regions=[
                Region(
                    path=region["path"],
                    start=region["startLine"],
                    end=region["endLine"],
                    name=region.get("name") or "",
                    kind=region.get("type") or "",
                )
                for region in result["properties"]["regions"]
            ],
        )
        for run in data["runs"]
        for result in run["results"]
    ]


def find_touched_groups(groups: list[Group], touched: TouchedLines) -> list[TouchedGroup]:
    """Keep the groups with a region that overlaps a touched line, most similar first."""
    touched_groups = []
    for group in groups:
        regions = sorted(group.regions, key=lambda r: (r.path, r.start))
        touched_regions = [r for r in regions if r.overlaps(touched.get(r.path, []))]
        if touched_regions:
            others = [r for r in regions if r not in touched_regions]
            touched_groups.append(TouchedGroup(group=group, touched=touched_regions, others=others))
    return sorted(touched_groups, key=lambda g: -g.group.similarity)


def format_markdown(touched_groups: list[TouchedGroup], similarity: int, min_lines: int) -> str:
    criteria = f"at least {similarity}% similar and {min_lines} lines long"
    if not touched_groups:
        return f"No duplicate code ({criteria}) found in lines this branch touched.\n"

    noun = "group" if len(touched_groups) == 1 else "groups"
    lines = [
        "## Duplicate code in lines this branch touched",
        "",
        f"{len(touched_groups)} duplicate {noun} ({criteria}). For each, consider merging the duplicates "
        "into shared code, or leave them if the similarity is superficial.",
    ]
    for number, touched_group in enumerate(touched_groups, start=1):
        lines += ["", f"### {number}. {touched_group.group.similarity:.1f}% similar", "", "Touched on this branch:"]
        lines += [_format_region(r) for r in touched_group.touched]
        if touched_group.others:
            lines += ["", "Similar to:"]
            lines += [_format_region(r) for r in touched_group.others]
    return "\n".join(lines) + "\n"


def _format_region(region: Region) -> str:
    label = f" `{region.name}`" if region.name else ""
    return f"- `{region.path}:{region.start}-{region.end}`{label}"


def format_json(touched_groups: list[TouchedGroup]) -> str:
    return json.dumps(
        [
            {
                "similarity": g.group.similarity,
                "touched": [asdict(r) for r in g.touched],
                "others": [asdict(r) for r in g.others],
            }
            for g in touched_groups
        ],
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default="apps", help="Directory to scan (default: apps)")
    parser.add_argument(
        "--base", help="Branch to diff against (default: origin/$GITHUB_BASE_REF if set, else origin/main)"
    )
    parser.add_argument("--similarity", type=int, default=90, help="Minimum percent similarity (default: 90)")
    parser.add_argument("--min-lines", type=int, default=5, help="Minimum lines in a region (default: 5)")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format")
    parser.add_argument("--fail", action="store_true", help="Exit with code 1 if any duplicates are reported")
    args = parser.parse_args(argv)
    base = resolve_base(args.base)

    try:
        repo_root = Path(_git(["rev-parse", "--show-toplevel"], repo_root=Path.cwd()).strip())
        touched = get_touched_lines(base=base, repo_root=repo_root)
    except subprocess.CalledProcessError as error:
        print(f"git {' '.join(error.cmd[3:])} failed: {error.stderr.strip()}", file=sys.stderr)
        print(
            f"Check that '{base}' exists: pass --base <branch>, or run `git fetch origin` to fetch it.",
            file=sys.stderr,
        )
        return 2
    try:
        sarif = run_treepeat(path=args.path, similarity=args.similarity, min_lines=args.min_lines, repo_root=repo_root)
        groups = parse_treepeat_sarif(sarif)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(f"treepeat failed with exit code {error.returncode}.", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"Could not read treepeat's SARIF output: {error!r}", file=sys.stderr)
        return 2
    touched_groups = find_touched_groups(groups=groups, touched=touched)

    if args.format == "json":
        print(format_json(touched_groups))
    else:
        print(format_markdown(touched_groups=touched_groups, similarity=args.similarity, min_lines=args.min_lines))

    return 1 if args.fail and touched_groups else 0


if __name__ == "__main__":
    sys.exit(main())
