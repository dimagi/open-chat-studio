#!/usr/bin/env python3
"""
Report where OCS's allauth template overrides have drifted from the installed upstream
templates.

OCS replaces allauth's markup wholesale (own design system, ``render_field`` tags,
``prelogin/auth_base.html``), so a textual diff is noise. What matters is *functional*
drift: a URL, form field, form action or context variable that upstream uses and the
override does not. Django renders unknown variables as the empty string, so this drift
fails silently -- a whole section of a page can quietly disappear after an allauth
upgrade.

Run after bumping django-allauth::

    python scripts/diff_allauth_overrides.py

Findings are candidates, not bugs. Each one needs a look at the upstream view: an
upstream-only URL is often just a form action OCS posts to implicitly, while an
upstream-only context variable usually is a real gap. Anything reported under
"OCS-only context variables" is a name upstream's template never reads -- confirm it is
in the view's context or the override is rendering nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import allauth

# Template roots allauth owns and OCS overrides.
TEMPLATE_ROOTS = ("account", "mfa", "socialaccount", "usersessions")

URL_RE = re.compile(r"{%\s*url\s+['\"]([\w:.-]+)['\"]")
FIELD_RE = re.compile(r"\bform\.([a-zA-Z_]\w*)")
ACTION_RE = re.compile(r"""name=["'](action_\w+)["']""")
VAR_RE = re.compile(r"{{\s*([a-zA-Z_]\w*)")
IF_RE = re.compile(r"{%\s*(?:if|elif)\s+([^%]+)%}")
FOR_RE = re.compile(r"{%\s*for\s+[\w, ]+\s+in\s+([\w.|]+)")
FOR_TARGET_RE = re.compile(r"{%\s*for\s+([\w, ]+?)\s+in\s")
QUOTED_RE = re.compile(r"""(['"]).*?\1""")
# Dotted paths, so only the root is treated as a context variable (`user.emailaddress_set.all`
# is a lookup on `user`, not three separate names).
PATH_RE = re.compile(r"\b([a-z_]\w*(?:\.\w+)*)")
# Names the template binds itself: `{% url ... as x %}`, `{% with x=... %}`, `count x=...`.
ASSIGNED_RE = re.compile(r"(?:\bas\s+([a-zA-Z_]\w*)\s*%})|(?:\b([a-zA-Z_]\w*)\s*=(?!=))")

# Bound form attributes, Django builtins, context processors and template keywords.
NOT_CONTEXT_VARS = frozenset(
    {
        "and",
        "as",
        "as_p",
        "block",
        "count",
        "csrf_token",
        "errors",
        "False",
        "fields",
        "forloop",
        "form",
        "if",
        "in",
        "is",
        "media",
        "messages",
        "non_field_errors",
        "None",
        "not",
        "or",
        "request",
        "settings",
        "True",
        "user",
        "view",
        "with",
    }
)


def find_upstream_templates() -> Path:
    return Path(allauth.__file__).parent / "templates"


def functional_signals(text: str) -> dict[str, set[str]]:
    return {
        "urls": set(URL_RE.findall(text)),
        "form fields": set(FIELD_RE.findall(text)) - NOT_CONTEXT_VARS,
        "form actions": set(ACTION_RE.findall(text)),
    }


def context_vars(text: str) -> set[str]:
    found = set(VAR_RE.findall(text)) | set(FOR_RE.findall(text))
    for condition in IF_RE.findall(text):
        found |= set(PATH_RE.findall(QUOTED_RE.sub("", condition)))
    roots = {name.split(".")[0].split("|")[0] for name in found}
    assigned = {name for match in ASSIGNED_RE.findall(text) for name in match if name}
    loop_targets = {name.strip() for targets in FOR_TARGET_RE.findall(text) for name in targets.split(",")}
    return roots - assigned - loop_targets - NOT_CONTEXT_VARS


def find_templates(root: Path) -> list[Path]:
    """Every allauth-owned template under ``root``, relative to it."""
    return sorted(
        path.relative_to(root)
        for template_root in TEMPLATE_ROOTS
        for path in (root / template_root).rglob("*")
        if path.is_file()
    )


def drift_between(ocs_text: str, up_text: str) -> dict[str, list[str]]:
    """What upstream uses and the override does not, plus what only the override reads."""
    ocs_signals = functional_signals(ocs_text)
    drift = {kind: sorted(up_values - ocs_signals[kind]) for kind, up_values in functional_signals(up_text).items()}
    drift = {f"upstream-only {kind}": values for kind, values in drift.items() if values}
    if extra_vars := sorted(context_vars(ocs_text) - context_vars(up_text)):
        drift["OCS-only context variables"] = extra_vars
    return drift


def report_drift(templates: Path, upstream: Path) -> tuple[int, list[Path]]:
    ocs_only: list[Path] = []
    findings = 0

    for rel in find_templates(templates):
        up = upstream / rel
        if not up.exists():
            ocs_only.append(rel)
            continue

        if drift := drift_between((templates / rel).read_text(), up.read_text()):
            findings += 1
            print(f"## {rel}")
            for kind, values in drift.items():
                print(f"   {kind}: {', '.join(values)}")
            print()

    return findings, ocs_only


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    templates = args.repo_root / "templates"
    upstream = find_upstream_templates()
    if not upstream.is_dir():
        print(f"upstream allauth templates not found at {upstream}", file=sys.stderr)
        return 1

    print(f"# allauth template drift (upstream: {upstream})\n")
    findings, ocs_only = report_drift(templates, upstream)

    print("## OCS-only templates (no upstream counterpart, nothing to compare)")
    for rel in ocs_only:
        print(f"   {rel}")

    print("\n## Upstream templates NOT overridden (OCS inherits allauth's markup)")
    for rel in find_templates(upstream):
        if not (templates / rel).exists():
            print(f"   {rel}")

    print(f"\n{findings} override(s) with drift to review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
