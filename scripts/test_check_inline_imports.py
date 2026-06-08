"""Tests for check_inline_imports.py.

Run with: uv run pytest scripts/test_check_inline_imports.py -v
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from check_inline_imports import (  # noqa: E402
    Candidate,
    HoistResult,
    InlineImport,
    check_package,
    claims_cycle,
    classify_candidates,
    detect_django_settings,
    find_import_root,
    find_inline_imports,
    find_module_time_imports,
    hoist_import,
    is_banned_import,
    load_banned_imports,
    main,
)


def make_pkg(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))


def check_one_import(root: Path, import_line: str, extra_files: dict[str, str] | None = None) -> list:
    """Build a package whose sole function makes ``import_line`` (plus any
    ``extra_files``) and return the findings — the common single-import case."""
    files = {"pkg/__init__.py": "", "pkg/a.py": f"def f():\n    {import_line}\n    return None\n"}
    files.update(extra_files or {})
    make_pkg(root, files)
    return check_package(root / "pkg")


class TestFindImportRoot:
    def test_top_level_package(self, tmp_path):
        make_pkg(tmp_path, {"pkg/__init__.py": ""})
        assert find_import_root(tmp_path / "pkg") == tmp_path

    def test_nested_package(self, tmp_path):
        make_pkg(
            tmp_path,
            {"apps/__init__.py": "", "apps/experiments/__init__.py": ""},
        )
        assert find_import_root(tmp_path / "apps" / "experiments") == tmp_path


class TestDetectDjangoSettings:
    def test_no_manage_py(self, tmp_path):
        assert detect_django_settings(tmp_path) is None

    def test_manage_py_with_settings(self, tmp_path):
        (tmp_path / "manage.py").write_text(
            'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n'
        )
        assert detect_django_settings(tmp_path) == "config.settings"

    def test_manage_py_single_quotes(self, tmp_path):
        (tmp_path / "manage.py").write_text(
            "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings.dev')\n"
        )
        assert detect_django_settings(tmp_path) == "proj.settings.dev"

    def test_manage_py_without_settings(self, tmp_path):
        (tmp_path / "manage.py").write_text("print('hello')\n")
        assert detect_django_settings(tmp_path) is None


class TestHoistImport:
    def test_multiple_statements_accumulate(self):
        source = '"""Doc."""\nx = 1\n'
        source = hoist_import(source, "import os")
        source = hoist_import(source, "import sys")
        lines = source.splitlines()
        assert lines[0] == '"""Doc."""'
        assert set(lines[1:3]) == {"import os", "import sys"}
        assert lines[3] == "x = 1"


def _candidate(name: str) -> Candidate:
    return Candidate(
        path=Path(f"{name}.py"),
        rel_path=f"{name}.py",
        inline_import=InlineImport(lineno=1, statement=f"import {name}"),
    )


class TestClassifyCandidates:
    def test_all_pass_uses_single_run(self):
        candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
        calls = []

        def verify(cands):
            calls.append(len(cands))
            return HoistResult(ok=True, error="")

        results = classify_candidates(candidates, verify)
        assert calls == [3]
        assert [hoistable for _, hoistable, _ in results] == [True] * 3

    def test_single_failure_isolated(self):
        candidates = [_candidate("a"), _candidate("bad"), _candidate("c")]

        def verify(cands):
            if any(c.rel_path == "bad.py" for c in cands):
                return HoistResult(ok=False, error="ImportError: cycle")
            return HoistResult(ok=True, error="")

        results = dict(
            (c.rel_path, (hoistable, error)) for c, hoistable, error in classify_candidates(candidates, verify)
        )
        assert results["a.py"] == (True, "")
        assert results["c.py"] == (True, "")
        assert results["bad.py"] == (False, "ImportError: cycle")

    def test_empty(self):
        assert classify_candidates([], lambda c: HoistResult(True, "")) == []


class TestClaimsCycle:
    def test_circular_prefix(self):
        assert claims_cycle("circular: a imports b")

    def test_circular_case_insensitive(self):
        assert claims_cycle("Circular dependency with models")

    def test_lazy_reason(self):
        assert not claims_cycle("lazy: heavy lib, slow startup")

    def test_timing_reason(self):
        assert not claims_cycle("deferred until app registry is ready")


class TestModuleTimeImports:
    def test_collects_module_class_and_try_scope(self):
        source = textwrap.dedent(
            """
            import os
            try:
                import fast_json
            except ImportError:
                import json
            class C:
                from collections import OrderedDict

            def f():
                import csv
            """
        )
        names = find_module_time_imports(source, "pkg.sub")
        assert {"os", "fast_json", "json", "collections"} <= names
        assert "csv" not in names

    def test_resolves_relative_imports(self):
        names = find_module_time_imports("from . import audio\nfrom .speech import stt\n", "apps.channels")
        assert "apps.channels.audio" in names
        assert "apps.channels.speech" in names

    def test_type_checking_blocks_excluded(self):
        # TYPE_CHECKING imports never execute at runtime; the else branch does.
        source = textwrap.dedent(
            """
            import typing
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                from twilio.rest import Client
            if typing.TYPE_CHECKING:
                import boto3
            else:
                import runtime_fallback
            """
        )
        names = find_module_time_imports(source, "pkg")
        assert "twilio.rest" not in names
        assert "boto3" not in names
        assert "runtime_fallback" in names


class TestJustificationVerification:
    """Justified imports are verified, not trusted (claims rot)."""

    def test_stale_circular_claim_fails(self, tmp_path):
        (finding,) = check_one_import(tmp_path, "import json  # noqa: PLC0415 - circular: b imports a")
        assert finding.verdict == "stale"

    def test_confirmed_circular_claim_is_justified(self, tmp_path):
        findings = check_one_import(
            tmp_path,
            "from pkg.b import g  # noqa: PLC0415 - circular: b imports a",
            {"pkg/b.py": "from pkg.a import f\ndef g():\n    return f\n"},
        )
        assert {f.path.name: f.verdict for f in findings}["a.py"] == "justified"

    def test_dubious_lazy_claim_warns(self, tmp_path):
        # json is claimed lazy in a.py but imported at module level in b.py.
        (finding,) = check_one_import(
            tmp_path, "import json  # noqa: PLC0415 - lazy: heavy lib", {"pkg/b.py": "import json\n"}
        )
        assert finding.verdict == "dubious"
        assert "b.py" in finding.error

    def test_valid_lazy_claim_is_justified(self, tmp_path):
        (finding,) = check_one_import(tmp_path, "import json  # noqa: PLC0415 - lazy: heavy lib")
        assert finding.verdict == "justified"

    def test_non_cost_claims_are_not_cross_referenced(self, tmp_path):
        # Timing/patching constraints are not falsifiable by the import index;
        # only `lazy:`-prefixed (cost) claims are.
        (finding,) = check_one_import(
            tmp_path,
            "import json  # noqa: PLC0415 - deferred until app registry is ready",
            {"pkg/b.py": "import json\n"},
        )
        assert finding.verdict == "justified"

    def test_imports_in_test_files_do_not_indict_lazy_claims(self, tmp_path):
        # Module-time imports in tests/factories/management commands are not
        # production startup cost, so they cannot make a lazy claim dubious.
        findings = check_one_import(
            tmp_path,
            "import json  # noqa: PLC0415 - lazy: heavy lib",
            {
                "pkg/tests/__init__.py": "",
                "pkg/tests/test_a.py": "import json\n",
                "pkg/tests/conftest.py": "import json\n",
                "pkg/factories/__init__.py": "import json\n",
                "pkg/management/commands/cmd.py": "import json\n",
            },
        )
        assert {f.path.name: f.verdict for f in findings}["a.py"] == "justified"

    def test_stale_justification_fails_ci(self, tmp_path, capsys, monkeypatch):
        check_one_import(tmp_path, "import json  # noqa: PLC0415 - circular: b imports a")
        monkeypatch.chdir(tmp_path)
        exit_code = main(["pkg"])
        assert exit_code == 1
        assert "stale" in capsys.readouterr().out

    def test_dubious_justification_warns_but_passes_ci(self, tmp_path, capsys, monkeypatch):
        check_one_import(tmp_path, "import json  # noqa: PLC0415 - lazy: heavy lib", {"pkg/b.py": "import json\n"})
        monkeypatch.chdir(tmp_path)
        exit_code = main(["pkg"])
        assert exit_code == 0
        assert "WARN" in capsys.readouterr().out


class TestLoadBannedImports:
    def test_no_pyproject(self, tmp_path):
        assert load_banned_imports(tmp_path) == ()

    def test_pyproject_without_setting(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        assert load_banned_imports(tmp_path) == ()

    def test_pyproject_with_setting(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.ruff.lint.flake8-tidy-imports]\nbanned-module-level-imports = ["boto3", "langchain_openai"]\n'
        )
        assert load_banned_imports(tmp_path) == ("boto3", "langchain_openai")


class TestIsBannedImport:
    BANNED = ("boto3", "langchain_openai")

    def _imp(self, source: str) -> InlineImport:
        (imp,) = find_inline_imports(f"def f():\n    {source}\n")
        return imp

    def test_exact_module(self):
        assert is_banned_import(self._imp("import boto3"), self.BANNED)

    def test_from_import(self):
        assert is_banned_import(self._imp("from langchain_openai import ChatOpenAI"), self.BANNED)

    def test_submodule(self):
        assert is_banned_import(self._imp("from boto3.s3.transfer import TransferConfig"), self.BANNED)

    def test_prefix_is_not_submodule(self):
        assert not is_banned_import(self._imp("import boto3_helpers"), self.BANNED)

    def test_unrelated_module(self):
        assert not is_banned_import(self._imp("import json"), self.BANNED)

    def test_relative_import(self):
        assert not is_banned_import(self._imp("from . import signals"), self.BANNED)


class TestFindInlineImports:
    def test_finds_function_imports(self):
        source = textwrap.dedent(
            """
            import os

            def f():
                import json
                return json
            """
        )
        imports = find_inline_imports(source)
        assert [i.statement for i in imports] == ["import json"]

    def test_nested_function_import_reported_once(self):
        source = textwrap.dedent(
            """
            def outer():
                def inner():
                    import json
                    return json
                return inner
            """
        )
        imports = find_inline_imports(source)
        assert [i.statement for i in imports] == ["import json"]

    def test_lazy_marker_justifies(self):
        source = textwrap.dedent(
            """
            def f():
                import json  # lazy-import: heavy module
            """
        )
        (imp,) = find_inline_imports(source)
        assert imp.justification == "heavy module"

    def test_noqa_dash_reason_justifies(self):
        source = textwrap.dedent(
            """
            def f():
                import json  # noqa: PLC0415 - lazy: heavy lib, slow startup
            """
        )
        (imp,) = find_inline_imports(source)
        assert imp.justification == "lazy: heavy lib, slow startup"

    def test_noqa_multiple_codes_with_reason_justifies(self):
        source = textwrap.dedent(
            """
            def f():
                from . import signals  # noqa: F401, PLC0415 - circular: x imports y
            """
        )
        (imp,) = find_inline_imports(source)
        assert imp.justification == "circular: x imports y"

    def test_bare_noqa_is_not_justified(self):
        source = textwrap.dedent(
            """
            def f():
                import json  # noqa: PLC0415
            """
        )
        (imp,) = find_inline_imports(source)
        assert imp.justification is None

    def test_noqa_without_plc0415_reason_not_justified(self):
        source = textwrap.dedent(
            """
            def f():
                import json  # noqa: F401 - some reason
            """
        )
        (imp,) = find_inline_imports(source)
        assert imp.justification is None


class TestCheckPackageEndToEnd:
    """End-to-end on synthetic non-Django packages (real subprocesses)."""

    def test_violation_and_legitimate(self, tmp_path):
        # b->a at module level; a->b inline. Hoisting a->b creates a real
        # cycle (legitimate). util's inline import hoists fine (violation).
        make_pkg(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": ("def f():\n    from pkg.b import g\n    return g\n"),
                "pkg/b.py": ("from pkg.a import f\ndef g():\n    return f\n"),
                "pkg/util.py": ("def h():\n    import json\n    return json\n"),
            },
        )
        findings = check_package(tmp_path / "pkg")
        verdicts = {f"{f.path.name}:{f.inline_import.statement}": f.verdict for f in findings}
        assert verdicts["a.py:from pkg.b import g"] == "legitimate"
        assert verdicts["util.py:import json"] == "violation"

    def test_subpackage_scan_imports_whole_tree(self, tmp_path):
        # Scanning a sub-package must still resolve absolute imports through
        # the top-level package (the original bug: No module named 'apps').
        make_pkg(
            tmp_path,
            {
                "apps/__init__.py": "",
                "apps/exp/__init__.py": "",
                "apps/exp/views.py": (
                    "from apps.other.models import X\ndef f():\n    import json\n    return json, X\n"
                ),
                "apps/other/__init__.py": "",
                "apps/other/models.py": "X = 1\n",
            },
        )
        findings = check_package(tmp_path / "apps" / "exp")
        (finding,) = findings
        assert finding.verdict == "violation"

    def test_migrations_skipped_for_django(self, tmp_path):
        (tmp_path / "manage.py").write_text(
            'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.missing")\n'
        )
        make_pkg(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/migrations/__init__.py": "",
                "pkg/migrations/0001_init.py": ("def forward(apps, schema_editor):\n    from pkg.models import X\n"),
                "pkg/models.py": "X = 1\n",
            },
        )
        findings = check_package(tmp_path / "pkg")
        assert all("migrations" not in f.path.parts for f in findings)

    def test_banned_imports_are_justified_without_verification(self, tmp_path):
        # A banned-module-level import is deliberately inline; it must be
        # skipped without spawning any verification subprocess.
        (tmp_path / "pyproject.toml").write_text(
            '[tool.ruff.lint.flake8-tidy-imports]\nbanned-module-level-imports = ["json"]\n'
        )
        make_pkg(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/m.py": ("def f():\n    import json\n    return json\n"),
            },
        )
        (finding,) = check_package(tmp_path / "pkg")
        assert finding.verdict == "justified"


def test_no_inline_imports(tmp_path):
    make_pkg(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": "import os\n"})
    assert check_package(tmp_path / "pkg") == []


class TestFilesMode:
    """--files: scan only the given files, verify against the whole tree."""

    def _make(self, tmp_path):
        make_pkg(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/one.py": ("def f():\n    import json\n    return json\n"),
                "pkg/two.py": ("def g():\n    import csv\n    return csv\n"),
            },
        )
        return tmp_path / "pkg"

    def test_only_listed_files_scanned(self, tmp_path):
        pkg = self._make(tmp_path)
        findings = check_package(pkg, files=(pkg / "one.py",))
        assert [f.path.name for f in findings] == ["one.py"]
        assert findings[0].verdict == "violation"

    def test_missing_file_skipped_with_note(self, tmp_path, capsys):
        pkg = self._make(tmp_path)
        findings = check_package(pkg, files=(pkg / "gone.py",))
        assert findings == []
        assert "gone.py" in capsys.readouterr().err

    def test_non_python_file_skipped_with_note(self, tmp_path, capsys):
        pkg = self._make(tmp_path)
        (pkg / "data.txt").write_text("not python")
        findings = check_package(pkg, files=(pkg / "data.txt",))
        assert findings == []
        assert "data.txt" in capsys.readouterr().err

    def test_file_outside_package_rejected(self, tmp_path):
        pkg = self._make(tmp_path)
        (tmp_path / "outside.py").write_text("x = 1\n")
        with pytest.raises(ValueError, match="outside.py"):
            check_package(pkg, files=(tmp_path / "outside.py",))

    def test_main_accepts_files_flag(self, tmp_path, capsys, monkeypatch):
        self._make(tmp_path)
        monkeypatch.chdir(tmp_path)
        exit_code = main(["pkg", "--files", "pkg/one.py"])
        out = capsys.readouterr().out
        assert exit_code == 1
        assert "one.py:2: `import json` hoists cleanly" in out
        assert "import csv" not in out

    def test_main_with_no_files_is_noop(self, tmp_path, capsys, monkeypatch):
        self._make(tmp_path)
        monkeypatch.chdir(tmp_path)
        exit_code = main(["pkg", "--files"])
        assert exit_code == 0
        assert "No inline imports found." in capsys.readouterr().out


class TestMainOutput:
    def test_only_hoistable_imports_printed(self, tmp_path, capsys, monkeypatch):
        # One legitimate cycle, one violation: only the violation is printed.
        make_pkg(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": ("def f():\n    from pkg.b import g\n    return g\n"),
                "pkg/b.py": ("from pkg.a import f\ndef g():\n    return f\n"),
                "pkg/util.py": ("def h():\n    import json\n    return json\n"),
            },
        )
        monkeypatch.chdir(tmp_path)
        exit_code = main(["pkg"])
        out = capsys.readouterr().out
        assert exit_code == 1
        assert "util.py:2: `import json` hoists cleanly" in out
        assert "from pkg.b import g" not in out  # legitimate: not printed
        assert "1 hoistable" in out
        assert "1 legitimate" in out
