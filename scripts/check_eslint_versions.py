#!/usr/bin/env python3
"""
Keep the eslint toolchain pinned identically in both places that run it.

ESLint has two entry points in this repo and they are versioned by different
ecosystems, so Dependabot updates them independently and they drift:

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
"""

from __future__ import annotations

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


def main() -> int:
    rev, pins = load_hook_pins()
    resolved = load_resolved_versions()

    failures = []
    eslint_pin = None

    for pin in pins:
        name, version = split_pin(pin)
        if name == "eslint":
            eslint_pin = version
        if not version:
            failures.append(f"{name}: pinned without a version in {PRE_COMMIT_CONFIG.name} (use `{name}@<version>`)")
            continue
        if name not in resolved:
            failures.append(
                f"{name}: pinned at {version} in {PRE_COMMIT_CONFIG.name} but absent from package.json "
                f"— drop the pin, or add the package back"
            )
            continue
        if resolved[name] != version:
            failures.append(
                f"{name}: {PRE_COMMIT_CONFIG.name} pins {version} but {PNPM_LOCK.name} resolves {resolved[name]}"
            )

    # The mirror's rev tracks the eslint release it wraps, so `rev: v10.8.0` must
    # agree with `eslint@10.8.0`; otherwise the hook runs a different binary than
    # the one its own dependency list claims.
    if eslint_pin is None:
        failures.append(f"eslint: not pinned in {PRE_COMMIT_CONFIG.name} additional_dependencies")
    elif rev.lstrip("v") != eslint_pin:
        failures.append(f"eslint: hook rev is {rev} but additional_dependencies pins eslint@{eslint_pin}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        print(
            f"\n{len(failures)} eslint version mismatch(es) between {PRE_COMMIT_CONFIG.name} and {PNPM_LOCK.name}."
            f"\nUpdate the `additional_dependencies` pins (and `rev`) to match the lockfile, or run"
            "\n`pnpm install` if package.json is the side that moved."
        )
        return 1

    print(f"eslint toolchain in sync: {', '.join(sorted(pins))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
