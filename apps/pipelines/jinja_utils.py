"""Jinja2 template validation and HTML linting utilities for pipeline nodes."""

import os
import tempfile
from pathlib import Path

from djlint.lint import lint_file
from djlint.settings import Config as DjlintConfig
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

# Curated djlint rules relevant to template fragments.
# All other rules (H005 html lang, H007 DOCTYPE, H016 title, J004/J018 url_for, etc.)
# are irrelevant for template fragments and would produce noise.
DJLINT_ALLOWED_RULES = {"H020", "H021", "H025", "T027", "T034"}

# Use /dev/shm (RAM-backed tmpfs on Linux) if available to avoid disk I/O per linting request
_DJLINT_TMPDIR = "/dev/shm" if os.path.isdir("/dev/shm") else None


def parse_jinja_template(template: str) -> TemplateSyntaxError | None:
    """Parse template AST without rendering. Returns error or None if valid."""
    try:
        SandboxedEnvironment().parse(template)
    except TemplateSyntaxError as e:
        return e
    return None


def djlint_check(template: str) -> list[dict]:
    """Run djlint on a template string and return lint issues as dicts.

    Uses a curated allowlist of rules (DJLINT_ALLOWED_RULES) to filter out
    rules that are irrelevant for template fragments.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, dir=_DJLINT_TMPDIR, encoding="utf-8") as f:
        f.write(template)
        tmp_path = Path(f.name)
    try:
        config = DjlintConfig(str(tmp_path), profile="jinja")
        results = lint_file(config, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    errors = []
    for issues in results.values():
        for issue in issues:
            code = issue.get("code", "")
            if code not in DJLINT_ALLOWED_RULES:
                continue
            line_str = issue.get("line", "1:0")
            parts = line_str.split(":")
            line = max(1, int(parts[0])) if parts[0].isdigit() else 1
            column = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            errors.append(
                {
                    "line": line,
                    "column": column,
                    "message": f"{code} {issue.get('message', '')}".strip(),
                    "severity": "warning",
                }
            )
    return errors
