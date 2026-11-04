#!/usr/bin/env python3
"""
Keep the eslint toolchain pinned identically in both places that run it.

ESLint has two entry points in this repo and they are versioned by different
ecosystems, so they can drift apart:

* ``package.json`` -> ``pnpm-lock.yaml`` — what ``pnpm run lint`` and the editor use.
* ``.pre-commit-config.yaml`` — the ``mirrors-eslint`` hook's ``rev`` plus its
  ``additional_dependencies``, which is what pre-commit and CI use.

Drift is quiet: both sides keep passing, but they enforce different rule sets, so
a commit that is clean locally fails in CI (or vice versa). Bumping ``@eslint/js``
across a major is the sharp case — it changes what ``eslint:recommended`` enables.

This compares the hook's pins against the versions ``pnpm-lock.yaml`` actually
resolves, rather than the ranges declared in ``package.json``. Exact-vs-exact: a
caret range that has floated above its declared version (``^8.65.0`` resolving to
``8.66.0``) is still caught.

Checks:

* every ``additional_dependencies`` pin equals the lockfile-resolved version;
* every pinned package is still a devDependency at all (catches a package dropped
  from package.json but left behind in the hook);
* the repo ``rev`` matches the pinned ``eslint`` version.

Dependabot only bumps the npm side (the pre-commit ecosystem ignores these hooks, see
``.github/dependabot.yml``). With ``--fix`` the hook's pins and ``rev`` are first rewritten
to the lockfile-resolved versions, then the checks above run; the ``sync-eslint-pins``
workflow does this on Dependabot's npm PRs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
PNPM_LOCK = REPO_ROOT / "pnpm-lock.yaml"

ESLINT_MIRROR = "https://github.com/pre-commit/mirrors-eslint"
HOOK_ID = "eslint"
# The lockfile importer for the root package.json. Nested packages (e.g.
# components/chat_widget) have their own importers and their own lockfiles.
ROOT_IMPORTER = "."

REPO_LINE = re.compile(r"\s*- repo:")
DEPENDENCIES_KEY = re.compile(r"\s*additional_dependencies:")
LIST_ITEM = re.compile(r"\s*-\s")
PIN_LINE = re.compile(r"(?P<head>\s*-\s*['\"]?)(?P<name>.+?)@(?P<version>[^'\"\s#]+)(?P<tail>['\"]?\s*(?:#.*)?\r?\n?)$")
REV_LINE = re.compile(r"(?P<head>\s*rev:\s*['\"]?v?)(?P<version>[^'\"\s#]+)")


def split_pin(pin: str) -> tuple[str, str]:
    """Split an ``additional_dependencies`` entry into (name, version).

    Scoped packages carry a leading ``@``, so the separator is the *last* ``@``:
    ``@eslint/js@10.0.1`` -> ``("@eslint/js", "10.0.1")``.
    """
    name, _, version = pin.rpartition("@")
    if not name:
        # No version pin at all, e.g. a bare "eslint".
        return pin, ""
    return name, version


def strip_peer_suffix(version: str) -> str:
    """Drop pnpm's peer-dependency suffix: ``10.0.1(eslint@10.8.0(jiti@2.7.0))`` -> ``10.0.1``."""
    return version.split("(", 1)[0]


def load_hook_pins() -> tuple[str, list[str]]:
    """Return the mirrors-eslint (rev, additional_dependencies) from the pre-commit config."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    for repo in config.get("repos", []):
        if repo.get("repo") != ESLINT_MIRROR:
            continue
        for hook in repo.get("hooks", []):
            if hook.get("id") == HOOK_ID:
                return repo.get("rev", ""), hook.get("additional_dependencies", [])
    raise SystemExit(f"FAIL {PRE_COMMIT_CONFIG.name}: no `{HOOK_ID}` hook found under {ESLINT_MIRROR}")


def load_resolved_versions() -> dict[str, str]:
    """Return {package: resolved version} for the root importer's dev + prod dependencies."""
    lock = yaml.safe_load(PNPM_LOCK.read_text(encoding="utf-8"))
    importer = lock.get("importers", {}).get(ROOT_IMPORTER)
    if importer is None:
        raise SystemExit(f"FAIL {PNPM_LOCK.name}: no `{ROOT_IMPORTER}` importer found")
    resolved = {}
    for section in ("devDependencies", "dependencies"):
        for name, entry in (importer.get(section) or {}).items():
            resolved[name] = strip_peer_suffix(entry["version"])
    return resolved


def hook_block(lines: list[str]) -> range:
    """Return the line range of the mirrors-eslint repo entry (empty if absent)."""
    starts = [i for i, line in enumerate(lines) if REPO_LINE.match(line)]
    for n, start in enumerate(starts):
        if ESLINT_MIRROR in lines[start]:
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            return range(start, end)
    return range(0)


def dependency_lines(lines: list[str], block: range) -> list[int]:
    """Return the indexes of the list items under the block's ``additional_dependencies:`` key."""
    keys = [i for i in block if DEPENDENCIES_KEY.match(lines[i])]
    if not keys:
        return []
    items = []
    for i in range(keys[0] + 1, block.stop):
        if LIST_ITEM.match(lines[i]):
            items.append(i)
        elif not is_blank_or_comment(lines[i]):
            break
    return items


def is_blank_or_comment(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def fix_pin_line(line: str, resolved: dict[str, str]) -> str:
    pin = PIN_LINE.match(line)
    if not pin or pin["name"] not in resolved:
        return line
    return f"{pin['head']}{pin['name']}@{resolved[pin['name']]}{pin['tail']}"


def fix_rev_line(line: str, eslint_version: str) -> str:
    rev = REV_LINE.match(line)
    if not rev:
        return line
    return f"{rev['head']}{eslint_version}{line[rev.end() :]}"


def fix_hook_pins(config_text: str, resolved: dict[str, str]) -> str:
    """Rewrite the mirrors-eslint pins in ``config_text`` to the lockfile-resolved versions.

    Edits the text in place so comments and quoting survive. Only the list items under
    ``additional_dependencies`` and the repo's ``rev`` are touched, and pins for packages absent
    from the lockfile are left alone for the check to report.
    """
    lines = config_text.splitlines(keepends=True)
    block = hook_block(lines)
    for i in dependency_lines(lines, block):
        lines[i] = fix_pin_line(lines[i], resolved)
    if "eslint" in resolved:
        for i in block:
            lines[i] = fix_rev_line(lines[i], resolved["eslint"])
    return "".join(lines)


def check_pin(pin: str, resolved: dict[str, str]) -> str | None:
    """Return the failure message for one hook pin, or None if it matches the lockfile."""
    name, version = split_pin(pin)
    if not version:
        return f"{name}: pinned without a version in {PRE_COMMIT_CONFIG.name} (use `{name}@<version>`)"
    if name not in resolved:
        return (
            f"{name}: pinned at {version} in {PRE_COMMIT_CONFIG.name} but absent from package.json "
            f"— drop the pin, or add the package back"
        )
    if resolved[name] != version:
        return f"{name}: {PRE_COMMIT_CONFIG.name} pins {version} but {PNPM_LOCK.name} resolves {resolved[name]}"
    return None


def check_rev(rev: str, pins: list[str]) -> str | None:
    """Return a failure message unless the mirror's ``rev`` agrees with the pinned eslint version.

    The mirror's rev tracks the eslint release it wraps, so ``rev: v10.8.0`` must agree with
    ``eslint@10.8.0``; otherwise the hook runs a different binary than its own dependency list claims.
    """
    eslint_versions = [version for name, version in map(split_pin, pins) if name == "eslint"]
    if not eslint_versions:
        return f"eslint: not pinned in {PRE_COMMIT_CONFIG.name} additional_dependencies"
    if rev.lstrip("v") != eslint_versions[-1]:
        return f"eslint: hook rev is {rev} but additional_dependencies pins eslint@{eslint_versions[-1]}"
    return None


def find_mismatches(rev: str, pins: list[str], resolved: dict[str, str]) -> list[str]:
    """Return a message for every hook pin that disagrees with the lockfile."""
    checks = [check_pin(pin, resolved) for pin in pins] + [check_rev(rev, pins)]
    return [check for check in checks if check]


def apply_fix() -> None:
    """Rewrite the pre-commit config's eslint pins in place to match the lockfile."""
    original = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    fixed = fix_hook_pins(original, load_resolved_versions())
    if fixed != original:
        PRE_COMMIT_CONFIG.write_text(fixed, encoding="utf-8")
        print(f"updated eslint pins in {PRE_COMMIT_CONFIG.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the pre-commit eslint pins against pnpm-lock.yaml.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help=f"rewrite the pins in {PRE_COMMIT_CONFIG.name} to the versions {PNPM_LOCK.name} resolves",
    )
    args = parser.parse_args(argv)

    if args.fix:
        apply_fix()

    rev, pins = load_hook_pins()
    failures = find_mismatches(rev, pins, load_resolved_versions())

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        print(
            f"\n{len(failures)} eslint version mismatch(es) between {PRE_COMMIT_CONFIG.name} and {PNPM_LOCK.name}."
            "\nRun `python scripts/check_eslint_versions.py --fix` to update the pins (and `rev`) to match the"
            "\nlockfile, or run `pnpm install` if package.json is the side that moved."
        )
        return 1

    print(f"eslint toolchain in sync: {', '.join(sorted(pins))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
