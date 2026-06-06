#!/usr/bin/env python3
"""
Verify whether inline (function-level) imports can safely move to module level.

Finds imports nested inside functions/methods, hoists a copy of each to module
level in a temporary copy of the package, and attempts to import every module
in the package. If the imports all succeed, the inline import is unjustified
(no real dependency cycle) and the check fails.

The package may be a sub-package (e.g. ``apps/experiments``): the whole
top-level package is copied and imported so intra-project absolute imports
resolve. If a ``manage.py`` with ``DJANGO_SETTINGS_MODULE`` exists at the
import root, ``django.setup()`` runs before importing and ``migrations/``
directories are skipped.

Justifications (`# noqa: PLC0415 - <reason>` or `# lazy-import: <reason>`)
are verified, not trusted — claims rot and AI-generated code invents them:
``circular:`` claims are hoist-tested (a clean hoist disproves the cycle →
FAIL); other claims are cost claims, fact-checked statically (the module
already imported at module-import time elsewhere → WARN). Only modules in
ruff's ``banned-module-level-imports`` (TID253) are config-blessed and
skipped. Only failing/warning imports are printed.

Verification is batched: all candidate imports are hoisted in one run first;
only if that fails does the script bisect to isolate the legitimate ones.
Hoisting is monotone (a set of hoists that imports cleanly implies every
subset does), so bisection is sound.

Usage:
    ./check_inline_imports.py ocs_deploy
    uv run python scripts/check_inline_imports.py apps/experiments
"""

import argparse
import ast
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LAZY_MARKER = re.compile(r"#\s*lazy-import:\s*(?P<reason>.+?)\s*$")
# The codebase's existing convention: `# noqa: PLC0415 - <reason>` (reason
# text after a dash also counts as a deliberate-inline justification).
NOQA_REASON = re.compile(r"#\s*noqa:?[A-Z0-9, ]*\bPLC0415\b[^#-]*-\s*(?P<reason>.+?)\s*$")
DJANGO_SETTINGS_RE = re.compile(r"""DJANGO_SETTINGS_MODULE['"]\s*,\s*['"](?P<module>[\w.]+)['"]""")
SUBPROCESS_TIMEOUT = 600


@dataclass
class InlineImport:
    lineno: int
    statement: str
    modules: tuple[str, ...] = ()  # absolute module names the statement imports
    justification: str | None = None


def find_inline_imports(source: str) -> list[InlineImport]:
    """Return all import statements nested inside function or method bodies.

    Imports in top-level ``if``/``try`` blocks are ignored: they already
    execute at module import time, so hoisting is moot.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    results = []

    def visit(parent: ast.AST, in_function: bool) -> None:
        for node in ast.iter_child_nodes(parent):
            if in_function and isinstance(node, (ast.Import, ast.ImportFrom)):
                justification = None
                for line in lines[node.lineno - 1 : node.end_lineno]:
                    if match := LAZY_MARKER.search(line) or NOQA_REASON.search(line):
                        justification = match.group("reason")
                        break
                if isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                else:  # relative imports have no absolute module name
                    modules = (node.module,) if node.level == 0 and node.module else ()
                results.append(
                    InlineImport(
                        lineno=node.lineno,
                        statement=ast.unparse(node),
                        modules=modules,
                        justification=justification,
                    )
                )
            visit(node, in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    visit(tree, False)
    return results


def hoist_import(source: str, statement: str) -> str:
    """Return source with a copy of ``statement`` added at module level.

    The original inline import is left in place (it becomes a no-op
    re-import), so no other code needs to move. The statement is inserted
    after the module docstring and any ``__future__`` imports.
    """
    tree = ast.parse(source)
    insert_lineno = 0  # insert before this 0-based line
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if is_docstring or is_future:
            insert_lineno = node.end_lineno
        else:
            break
    lines = source.splitlines(keepends=True)
    lines.insert(insert_lineno, statement + "\n")
    return "".join(lines)


def find_import_root(package_dir: Path) -> Path:
    """Return the nearest ancestor directory that is not itself a package.

    Walking up past ``__init__.py`` files finds the directory that must be on
    ``sys.path`` for the package's absolute imports to resolve (e.g. the repo
    root for ``apps/experiments``).
    """
    root = package_dir.resolve()
    while (root / "__init__.py").exists():
        root = root.parent
    return root


def detect_django_settings(import_root: Path) -> str | None:
    """Return the DJANGO_SETTINGS_MODULE declared in manage.py, if any."""
    manage = import_root / "manage.py"
    if not manage.exists():
        return None
    match = DJANGO_SETTINGS_RE.search(manage.read_text(encoding="utf-8-sig"))
    return match.group("module") if match else None


def load_banned_imports(import_root: Path) -> tuple[str, ...]:
    """Return ruff's banned-module-level-imports from pyproject.toml, if any.

    Modules listed there (TID253) are deliberately imported inside functions,
    so inline imports of them need no verification.
    """
    pyproject = import_root / "pyproject.toml"
    if not pyproject.exists():
        return ()
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    for key in ("tool", "ruff", "lint", "flake8-tidy-imports"):
        config = config.get(key, {}) if isinstance(config, dict) else {}
    return tuple(config.get("banned-module-level-imports", ()))


def is_banned_import(imp: InlineImport, banned: tuple[str, ...]) -> bool:
    """Whether the import targets a banned module or one of its submodules."""
    return any(module == ban or module.startswith(ban + ".") for module in imp.modules for ban in banned)


def claims_cycle(justification: str) -> bool:
    """Whether a justification claims a circular import (empirically testable)."""
    return bool(re.match(r"circular\b", justification.strip(), re.IGNORECASE))


def claims_cost(justification: str) -> bool:
    """Whether a justification claims import cost (checkable via the index).

    Other claims (timing constraints, test patching) are not falsifiable by
    either method and are trusted as long as a reason is given.
    """
    return bool(re.match(r"lazy\b", justification.strip(), re.IGNORECASE))


def _is_type_checking_test(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def find_module_time_imports(source: str, module_package: str) -> set[str]:
    """Return dotted names imported at module-import time.

    Module, class, and top-level ``try``/``if`` scope all execute on import;
    function bodies and ``if TYPE_CHECKING:`` blocks (never executed at
    runtime) are excluded. Relative imports are resolved against
    ``module_package`` (the importing module's package).
    """
    tree = ast.parse(source)
    names: set[str] = set()

    def visit(parent: ast.AST, in_function: bool) -> None:
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, ast.If) and _is_type_checking_test(node.test):
                # only the else branch runs at runtime
                visit(ast.Module(body=node.orelse, type_ignores=[]), in_function)
                continue
            if not in_function:
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        names.add(node.module)
                    elif node.level:
                        parts = module_package.split(".")[: len(module_package.split(".")) - (node.level - 1)]
                        base = ".".join(parts + ([node.module] if node.module else []))
                        names.add(base)
                        # `from . import x` may import submodule x
                        names.update(f"{base}.{alias.name}" for alias in node.names)
            visit(node, in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    visit(tree, False)
    return names


def _is_startup_relevant(rel: Path) -> bool:
    """Whether a file's module-time imports count as production startup cost.

    Tests, factories, conftest, and management commands only load on demand,
    so their imports cannot make a ``lazy:`` claim dubious.
    """
    if any(part in ("tests", "factories", "management", "migrations") for part in rel.parts):
        return False
    return not (rel.name.startswith("test_") or rel.name == "conftest.py")


def module_time_import_index(ctx: "PackageContext") -> dict[str, Path]:
    """Map every module-time-imported dotted name in the tree to one importer.

    Used to fact-check ``lazy:`` justifications: a module already imported at
    module level elsewhere costs nothing extra to import at module level here.
    """
    index: dict[str, Path] = {}
    for path in sorted(ctx.top_package.rglob("*.py")):
        rel = path.relative_to(ctx.top_package)
        if not _is_startup_relevant(rel):
            continue
        parts = (ctx.top_package.name, *rel.with_suffix("").parts)
        module_package = ".".join(parts[:-1])
        # utf-8-sig: tolerate files saved with a UTF-8 BOM
        for name in find_module_time_imports(path.read_text(encoding="utf-8-sig"), module_package):
            index.setdefault(name, path)
    return index


def loaded_at_module_time(modules: tuple[str, ...], index: dict[str, Path]) -> tuple[str, Path] | None:
    """Return (module, importer) if any of ``modules`` is provably loaded at
    module-import time somewhere in the tree (itself or a submodule of it)."""
    for module in modules:
        for name, path in index.items():
            if name == module or name.startswith(module + "."):
                return module, path
    return None


@dataclass
class PackageContext:
    """Resolved layout of the package under test."""

    package_dir: Path  # the scanned (sub-)package, as given on the CLI
    import_root: Path  # directory that must be on sys.path
    top_package: Path  # top-level package containing package_dir
    django_settings: str | None
    banned_imports: tuple[str, ...]  # ruff banned-module-level-imports
    exclude: tuple[str, ...]  # globs relative to package_dir

    @classmethod
    def resolve(cls, package_dir: Path, exclude: tuple[str, ...]) -> "PackageContext":
        resolved = package_dir.resolve()
        import_root = find_import_root(resolved)
        top_name = resolved.relative_to(import_root).parts[0]
        return cls(
            package_dir=package_dir,
            import_root=import_root,
            top_package=import_root / top_name,
            django_settings=detect_django_settings(import_root),
            banned_imports=load_banned_imports(import_root),
            exclude=exclude,
        )

    def skip(self, rel_to_scan: Path) -> bool:
        """Whether a file (relative to the scanned package) should be ignored."""
        if self.django_settings and "migrations" in rel_to_scan.parts:
            return True
        rel = rel_to_scan.as_posix()
        return any(fnmatch.fnmatch(rel, pattern) for pattern in self.exclude)


@dataclass
class HoistResult:
    ok: bool
    error: str


@dataclass
class Candidate:
    path: Path  # original file path, for reporting
    rel_path: str  # path relative to the top-level package
    inline_import: InlineImport


def discover_modules(pkg_copy: Path, ctx: PackageContext) -> list[str]:
    """Return dotted module names for every importable .py file in the copy."""
    scan_rel = ctx.package_dir.resolve().relative_to(ctx.top_package)
    modules = []
    for path in sorted(pkg_copy.rglob("*.py")):
        rel = path.relative_to(pkg_copy)
        if ctx.django_settings and "migrations" in rel.parts:
            continue
        try:
            rel_to_scan = rel.relative_to(scan_rel)
        except ValueError:
            pass  # outside the scanned sub-package; excludes don't apply
        else:
            if ctx.skip(rel_to_scan):
                continue
        parts = (pkg_copy.name, *rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append(".".join(parts))
    return modules


def _bootstrap_code(tmp: str, ctx: PackageContext, modules: list[str]) -> str:
    """Build the subprocess code: shadow the real package, set up Django,
    import everything."""
    lines = ["import sys", f"sys.path.insert(0, {tmp!r})"]
    if ctx.django_settings:
        lines += [
            "import os",
            f"os.environ.setdefault('DJANGO_SETTINGS_MODULE', {ctx.django_settings!r})",
            "import django",
            "django.setup()",
        ]
    lines.append("import importlib")
    lines += [f"importlib.import_module({m!r})" for m in modules]
    return "\n".join(lines)


def _import_all_from_copy(ctx: PackageContext, mutate=None) -> HoistResult:
    """Copy the top-level package to a temp dir, optionally mutate it, import
    everything.

    Importing every module (not just an edited one) matters: an import cycle
    can fail through one entry point and succeed through another. The temp
    copy is placed first on ``sys.path`` so it shadows the real package, while
    the import root (cwd) still provides sibling top-level modules such as
    Django settings.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pkg_copy = Path(tmp) / ctx.top_package.name
        shutil.copytree(ctx.top_package, pkg_copy)
        if mutate is not None:
            mutate(pkg_copy)
        code = _bootstrap_code(tmp, ctx, discover_modules(pkg_copy, ctx))
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ctx.import_root,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return HoistResult(ok=False, error="import run timed out")
        if proc.returncode == 0:
            return HoistResult(ok=True, error="")
        return HoistResult(ok=False, error=proc.stderr.strip())


def verify_hoists(ctx: PackageContext, candidates: list[Candidate]) -> HoistResult:
    """Empirically test whether hoisting all ``candidates`` breaks any import."""

    def apply_hoists(pkg_copy: Path) -> None:
        by_file: dict[str, list[str]] = {}
        for candidate in candidates:
            by_file.setdefault(candidate.rel_path, []).append(candidate.inline_import.statement)
        for rel_path, statements in by_file.items():
            target = pkg_copy / rel_path
            # utf-8-sig: tolerate files saved with a UTF-8 BOM
            source = target.read_text(encoding="utf-8-sig")
            for statement in statements:
                source = hoist_import(source, statement)
            target.write_text(source)

    return _import_all_from_copy(ctx, mutate=apply_hoists)


def classify_candidates(
    candidates: list[Candidate],
    verify: Callable[[list[Candidate]], HoistResult],
) -> list[tuple[Candidate, bool, str]]:
    """Return (candidate, hoistable, error) triples using batched bisection.

    All candidates are hoisted together first; in the common case (everything
    hoists cleanly) this costs a single run. On failure the set is bisected:
    hoisting is monotone, so a set that imports cleanly proves every member
    individually hoistable.
    """
    if not candidates:
        return []
    result = verify(candidates)
    if result.ok:
        return [(c, True, "") for c in candidates]
    if len(candidates) == 1:
        return [(candidates[0], False, result.error)]
    mid = len(candidates) // 2
    return classify_candidates(candidates[:mid], verify) + classify_candidates(candidates[mid:], verify)


@dataclass
class Finding:
    path: Path
    inline_import: InlineImport
    # "violation"    unjustified and hoists cleanly                  -> fail
    # "stale"        claims a cycle that empirically does not exist  -> fail
    # "dubious"      claims lazy cost but module loads at startup    -> warn
    # "legitimate"   unjustified but genuinely cannot be hoisted
    # "justified"    claim checked (or config-blessed) and holds up
    # "inconclusive" baseline import fails; nothing was proven       -> exit 2
    verdict: str
    error: str = ""


def _scan_paths(package_dir: Path, files: tuple[Path, ...] | None) -> list[Path]:
    """Return the files to scan: the whole package, or just ``files``.

    Missing and non-Python files are skipped with a note (CI may pass deleted
    or renamed paths); files outside the package are an error.
    """
    if files is None:
        return sorted(package_dir.rglob("*.py"))
    paths = []
    for path in sorted(files):
        if path.suffix != ".py" or not path.exists():
            print(f"note: skipping {path} (missing or not a .py file)", file=sys.stderr)
            continue
        if not path.resolve().is_relative_to(package_dir.resolve()):
            raise ValueError(f"{path} is not inside {package_dir}")
        paths.append(path)
    return paths


def check_package(
    package_dir: Path,
    exclude: tuple[str, ...] = (),
    files: tuple[Path, ...] | None = None,
) -> list[Finding]:
    """Find every inline import in the package and verify each one.

    ``files`` restricts which files are scanned for inline imports (e.g. the
    files changed in a PR); hoists are still verified against the whole
    top-level package, since import cycles span sub-packages.

    The hoisted runs are compared against a baseline run of the unmodified
    package in the same environment. If the baseline itself fails, the
    empirical test proves nothing and findings are marked inconclusive.
    """
    ctx = PackageContext.resolve(package_dir, exclude)
    findings = []
    empirical = []  # unjustified, or justified with a (testable) cycle claim
    lazy_claimed = []
    for path in _scan_paths(package_dir, files):
        if ctx.skip(path.resolve().relative_to(package_dir.resolve())):
            continue
        rel_path = str(path.resolve().relative_to(ctx.top_package))
        # utf-8-sig: tolerate files saved with a UTF-8 BOM
        for imp in find_inline_imports(path.read_text(encoding="utf-8-sig")):
            if is_banned_import(imp, ctx.banned_imports):
                # config-blessed: ruff TID253 forces these to stay inline
                findings.append(Finding(path, imp, "justified"))
            elif imp.justification is None or claims_cycle(imp.justification):
                empirical.append(Candidate(path, rel_path, imp))
            elif claims_cost(imp.justification):
                lazy_claimed.append(Candidate(path, rel_path, imp))
            else:
                # timing/patching constraints: not falsifiable, trusted
                findings.append(Finding(path, imp, "justified"))

    # Fact-check lazy/cost claims statically: a module already imported at
    # module-import time elsewhere costs nothing extra to hoist.
    if lazy_claimed:
        index = module_time_import_index(ctx)
        for c in lazy_claimed:
            hit = loaded_at_module_time(c.inline_import.modules, index)
            if hit is None:
                findings.append(Finding(c.path, c.inline_import, "justified"))
            else:
                module, importer = hit
                rel_importer = importer.relative_to(ctx.import_root)
                findings.append(
                    Finding(
                        c.path,
                        c.inline_import,
                        "dubious",
                        f"`{module}` is already imported at module level in {rel_importer}",
                    )
                )

    # Empirically test the rest by hoisting: cycle claims must reproduce.
    if empirical:
        baseline = _import_all_from_copy(ctx)
        if not baseline.ok:
            findings += [Finding(c.path, c.inline_import, "inconclusive", baseline.error) for c in empirical]
        else:
            for c, hoistable, error in classify_candidates(empirical, lambda cands: verify_hoists(ctx, cands)):
                if c.inline_import.justification is None:
                    verdict = "violation" if hoistable else "legitimate"
                else:
                    verdict = "stale" if hoistable else "justified"
                findings.append(Finding(c.path, c.inline_import, verdict, error))
    findings.sort(key=lambda f: (str(f.path), f.inline_import.lineno))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Package directory to check")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Glob (relative to the package) of files to skip entirely,"
        " e.g. 'lambdas/*' for standalone Lambda entry points. Repeatable.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        type=Path,
        default=None,
        metavar="FILE",
        help="Only scan these files for inline imports (e.g. the files changed"
        " in a PR). Hoists are still verified against the whole package.",
    )
    args = parser.parse_args(argv)

    django_settings = detect_django_settings(find_import_root(args.package.resolve()))
    if django_settings:
        print(f"Django project detected (settings: {django_settings})", file=sys.stderr)

    files = tuple(args.files) if args.files is not None else None
    findings = check_package(args.package, exclude=tuple(args.exclude), files=files)
    counts = Counter(f.verdict for f in findings)
    for f in findings:
        loc = f"{f.path}:{f.inline_import.lineno}"
        last_error = f.error.splitlines()[-1] if f.error else ""
        if f.verdict == "violation":
            print(f"FAIL {loc}: `{f.inline_import.statement}` hoists cleanly")
            print("     Move it to module level, or justify it with `# noqa: PLC0415 - <reason>`.")
        elif f.verdict == "stale":
            print(
                f"FAIL {loc}: `{f.inline_import.statement}` is justified as"
                f' "{f.inline_import.justification}" but hoists cleanly — stale justification'
            )
            print("     Move it to module level, or correct the justification.")
        elif f.verdict == "dubious":
            print(
                f"WARN {loc}: `{f.inline_import.statement}` is justified as"
                f' "{f.inline_import.justification}" but {last_error}'
            )
        elif f.verdict == "inconclusive":
            print(
                f"???  {loc}: `{f.inline_import.statement}` could not be"
                " verified; the package fails to import even without hoisting:"
            )
            print(f"     {last_error}")
    if findings:
        print(
            f"Checked {len(findings)} inline imports:"
            f" {counts['violation']} hoistable, {counts['stale']} stale,"
            f" {counts['dubious']} dubious, {counts['legitimate']} legitimate,"
            f" {counts['justified']} justified."
        )
    else:
        print("No inline imports found.")
    if counts["violation"] or counts["stale"]:
        return 1
    if counts["inconclusive"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
