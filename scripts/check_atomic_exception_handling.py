#!/usr/bin/env python3
"""
Flag database exceptions caught *inside* a ``transaction.atomic()`` block.

When a query fails inside an atomic block, PostgreSQL aborts the transaction and
Django sets ``connection.needs_rollback``. Catching that exception without
leaving the block means the rollback never happens at the right level, so:

* any later query in the block raises ``TransactionManagementError:
  An error occurred in the current transaction. You can't execute queries until
  the end of the 'atomic' block``; and
* if the failure came from a path Django does not wrap in
  ``mark_for_rollback_on_error`` (raw SQL, deferred constraints), the block's own
  exit raises ``InternalError: current transaction is aborted``.

Broken::

    with transaction.atomic():
        try:
            do_stuff()
        except IntegrityError:
            handle()

Fixed — try outside the atomic block::

    try:
        with transaction.atomic():
            do_stuff()
    except IntegrityError:
        handle()

Fixed — nested atomic as a savepoint, when the outer block must continue::

    with transaction.atomic():
        try:
            with transaction.atomic():
                do_stuff()
        except IntegrityError:
            handle()

A handler is also accepted when it cannot leave a broken transaction behind:
it re-raises unconditionally, or it calls ``transaction.set_rollback(True)``.

Escape hatch for the rare genuine exception (nothing in the ``try`` body touches
the database, for instance) — put a marker comment on the ``try:`` line or on the
``except`` clause::

    try:  # atomic-catch-ok: no DB access, parses an API response
        ...

Usage:
    uv run python scripts/check_atomic_exception_handling.py apps
    uv run python scripts/check_atomic_exception_handling.py apps --files apps/foo/views.py
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Exception types whose presence means the transaction may already be broken.
# `Error` is psycopg's/Django's base DB error; the bare-except and broad
# `Exception`/`BaseException` cases are handled separately.
DB_EXCEPTIONS = frozenset(
    {
        "DatabaseError",
        "DataError",
        "Error",
        "IntegrityError",
        "InterfaceError",
        "InternalError",
        "NotSupportedError",
        "OperationalError",
        "ProgrammingError",
        "TransactionManagementError",
    }
)
BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
MARKER = re.compile(r"#\s*atomic-catch-ok:\s*(?P<reason>.+?)\s*$")


@dataclass
class Finding:
    path: Path
    lineno: int
    atomic_lineno: int
    caught: str  # what the handler catches, e.g. "IntegrityError" or "bare except"
    function: str  # enclosing function, for the message


def _attr_name(node: ast.AST) -> str:
    """Rightmost name of a (possibly dotted) expression: ``db.IntegrityError`` -> ``IntegrityError``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_atomic_call(node: ast.AST) -> bool:
    """Whether an expression is ``transaction.atomic``/``atomic``, called or not.

    Matches both ``atomic`` and ``atomic()`` so it works for decorators
    (``@transaction.atomic`` and ``@transaction.atomic()``) and ``with`` items.
    """
    target = node.func if isinstance(node, ast.Call) else node
    return _attr_name(target) == "atomic"


def _opens_atomic(node: ast.AST) -> bool:
    """Whether a ``with``/``async with`` statement opens an atomic block."""
    return isinstance(node, (ast.With, ast.AsyncWith)) and any(
        _is_atomic_call(item.context_expr) for item in node.items
    )


def _is_atomic_decorated(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        _is_atomic_call(d) for d in node.decorator_list
    )


def _caught_names(handler: ast.ExceptHandler) -> list[str]:
    """Exception names a handler catches; ``[]`` for a bare ``except:``."""
    if handler.type is None:
        return []
    if isinstance(handler.type, ast.Tuple):
        return [_attr_name(elt) for elt in handler.type.elts]
    return [_attr_name(handler.type)]


def _catches_db_error(handler: ast.ExceptHandler) -> str | None:
    """What makes this handler risky inside an atomic block, or None if it isn't."""
    names = _caught_names(handler)
    if not names:
        return "bare except"
    for name in names:
        if name in DB_EXCEPTIONS or name in BROAD_EXCEPTIONS:
            return name
    return None


def _always_reraises(handler: ast.ExceptHandler) -> bool:
    """Whether the handler unconditionally re-raises, letting the atomic block roll back.

    Only a ``raise`` at the top level of the handler counts: a ``raise`` nested in
    an ``if`` (``if not fail_silently: raise``) leaves the swallowing path intact.
    """
    return any(isinstance(stmt, ast.Raise) for stmt in handler.body)


def _marks_rollback(handler: ast.ExceptHandler) -> bool:
    """Whether the handler calls ``transaction.set_rollback(...)``, which is the
    documented way to force the enclosing block to roll back after swallowing."""
    return any(
        _attr_name(node.func) == "set_rollback"
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
        if isinstance(node, ast.Call)
    )


def _body_is_savepointed(try_node: ast.Try) -> bool:
    """Whether every statement in the ``try`` body sits inside a nested atomic block.

    This is the savepoint pattern (option B): the inner block rolls back to its
    savepoint on the way out, so the outer transaction stays usable.
    """
    return bool(try_node.body) and all(_opens_atomic(stmt) for stmt in try_node.body)


def _has_marker(lines: list[str], *nodes: ast.AST) -> bool:
    """Whether an ``atomic-catch-ok:`` marker sits on any of the given nodes' first lines."""
    return any(MARKER.search(lines[node.lineno - 1]) for node in nodes if node.lineno - 1 < len(lines))


def find_violations(source: str, path: Path) -> list[Finding]:
    """Return every DB-exception handler nested inside an atomic block."""
    tree = ast.parse(source)
    lines = source.splitlines()
    findings: list[Finding] = []

    def visit(node: ast.AST, atomic_lineno: int | None, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_function = function
            child_atomic = atomic_lineno
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_function = child.name
                # A nested function body runs wherever it is called, not here, so it
                # only inherits an atomic scope from its own decorator.
                child_atomic = child.lineno if _is_atomic_decorated(child) else None
            elif _opens_atomic(child):
                child_atomic = child.lineno
            elif isinstance(child, ast.Try) and atomic_lineno is not None:
                findings.extend(_check_try(child, atomic_lineno, function, lines, path))
                # The try body is still visited below: a nested atomic inside it
                # opens a new scope that its own handlers are measured against.
            visit(child, child_atomic, child_function)

    visit(tree, None, "<module>")
    return sorted(findings, key=lambda f: f.lineno)


def _check_try(try_node: ast.Try, atomic_lineno: int, function: str, lines: list[str], path: Path) -> list[Finding]:
    if _body_is_savepointed(try_node):
        return []
    findings = []
    for handler in try_node.handlers:
        caught = _catches_db_error(handler)
        if caught is None or _always_reraises(handler) or _marks_rollback(handler):
            continue
        if _has_marker(lines, try_node, handler):
            continue
        findings.append(
            Finding(
                path=path,
                lineno=handler.lineno,
                atomic_lineno=atomic_lineno,
                caught=caught,
                function=function,
            )
        )
    return findings


def _scan_paths(root: Path, files: tuple[Path, ...] | None) -> list[Path]:
    """Return the files to scan: everything under ``root``, or just ``files``.

    Missing/non-Python paths are skipped (CI may pass deleted or renamed files),
    as are migrations, which run inside their own transaction management.
    """
    candidates = sorted(root.rglob("*.py")) if files is None else sorted(files)
    paths = []
    for path in candidates:
        if path.suffix != ".py" or not path.exists() or "migrations" in path.parts:
            continue
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, nargs="?", default=Path("apps"), help="Directory to scan")
    parser.add_argument(
        "--files",
        nargs="*",
        type=Path,
        default=None,
        metavar="FILE",
        help="Only check these files (e.g. the files changed in a PR)",
    )
    args = parser.parse_args(argv)

    findings = []
    for path in _scan_paths(args.root, tuple(args.files) if args.files is not None else None):
        # utf-8-sig: tolerate files saved with a UTF-8 BOM
        try:
            findings += find_violations(path.read_text(encoding="utf-8-sig"), path)
        except SyntaxError as exc:
            print(f"SKIP {path}: could not parse ({exc})", file=sys.stderr)

    for f in findings:
        print(
            f"FAIL {f.path}:{f.lineno}: `except {f.caught}` inside the atomic block opened at "
            f"line {f.atomic_lineno} ({f.function})"
        )
    if findings:
        print(
            f"\n{len(findings)} database exception(s) caught inside an atomic block."
            "\nMove the try/except outside the atomic block, or wrap the failing code in a nested"
            "\n`transaction.atomic()` savepoint. See scripts/check_atomic_exception_handling.py for details."
        )
        return 1
    print("No database exceptions caught inside atomic blocks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
