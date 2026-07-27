"""Tests for check_atomic_exception_handling.py.

Run with: uv run pytest scripts/test_check_atomic_exception_handling.py -v
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from check_atomic_exception_handling import find_violations, main  # noqa: E402


def violations(source: str) -> list:
    return find_violations(textwrap.dedent(source), Path("example.py"))


class TestBrokenPattern:
    @pytest.mark.parametrize(
        "handler",
        [
            pytest.param("except IntegrityError:", id="integrity-error"),
            pytest.param("except DatabaseError:", id="database-error"),
            pytest.param("except db.IntegrityError:", id="dotted"),
            pytest.param("except (ValueError, IntegrityError):", id="tuple"),
            pytest.param("except Exception:", id="broad"),
            pytest.param("except:", id="bare"),
            pytest.param("except IntegrityError as exc:", id="aliased"),
        ],
    )
    def test_catch_inside_with_block(self, handler):
        found = violations(f"""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    {handler}
                        handle()
        """)
        assert len(found) == 1

    def test_catch_inside_decorated_function(self):
        found = violations("""
            @transaction.atomic()
            def f():
                try:
                    save()
                except IntegrityError:
                    handle()
        """)
        assert len(found) == 1
        assert found[0].function == "f"

    def test_bare_atomic_decorator(self):
        found = violations("""
            @transaction.atomic
            def f():
                try:
                    save()
                except IntegrityError:
                    handle()
        """)
        assert len(found) == 1

    def test_async_with(self):
        found = violations("""
            async def f():
                async with transaction.atomic():
                    try:
                        await save()
                    except IntegrityError:
                        handle()
        """)
        assert len(found) == 1

    def test_atomic_as_one_of_several_context_managers(self):
        found = violations("""
            def f():
                with transaction.atomic(), current_team(team):
                    try:
                        save()
                    except IntegrityError:
                        handle()
        """)
        assert len(found) == 1

    def test_deeply_nested_try(self):
        found = violations("""
            def f():
                with transaction.atomic():
                    for item in items:
                        if item:
                            try:
                                save(item)
                            except IntegrityError:
                                handle()
        """)
        assert len(found) == 1

    def test_reports_both_the_handler_and_the_atomic_line(self):
        found = violations("""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        handle()
        """)
        assert (found[0].lineno, found[0].atomic_lineno) == (6, 3)

    def test_each_risky_handler_is_reported(self):
        found = violations("""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        handle()
                    except Exception:
                        handle()
        """)
        assert [f.caught for f in found] == ["IntegrityError", "Exception"]


class TestAcceptedPatterns:
    def test_try_outside_atomic(self):
        assert not violations("""
            def f():
                try:
                    with transaction.atomic():
                        save()
                except IntegrityError:
                    handle()
        """)

    def test_nested_atomic_savepoint(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        with transaction.atomic():
                            save()
                    except IntegrityError:
                        handle()
        """)

    def test_savepoint_with_keyword_argument(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        with transaction.atomic(savepoint=True):
                            save()
                    except IntegrityError:
                        handle()
        """)

    def test_handler_that_reraises(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        log.exception("boom")
                        raise
        """)

    def test_handler_that_sets_rollback(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        transaction.set_rollback(True)
                        handle()
        """)

    def test_non_database_exception(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        parse(payload)
                    except (KeyError, ValueError):
                        handle()
        """)

    def test_try_outside_any_atomic_block(self):
        assert not violations("""
            def f():
                try:
                    save()
                except IntegrityError:
                    handle()
        """)

    def test_non_atomic_context_manager(self):
        assert not violations("""
            def f():
                with open(path) as fh:
                    try:
                        save(fh)
                    except IntegrityError:
                        handle()
        """)

    def test_marker_comment_on_try(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:  # atomic-catch-ok: no DB access, parses an API response
                        parse(payload)
                    except Exception:
                        handle()
        """)

    def test_marker_comment_on_handler(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    try:
                        parse(payload)
                    except Exception:  # atomic-catch-ok: no DB access
                        handle()
        """)

    def test_marker_without_reason_does_not_count(self):
        assert violations("""
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except Exception:  # atomic-catch-ok
                        handle()
        """)


class TestScopeTracking:
    def test_nested_function_does_not_inherit_the_atomic_scope(self):
        """A closure defined inside an atomic block runs wherever it is called."""
        assert not violations("""
            def f():
                with transaction.atomic():
                    def callback():
                        try:
                            save()
                        except IntegrityError:
                            handle()
                    register(callback)
        """)

    def test_sibling_method_does_not_inherit_the_decorator(self):
        assert not violations("""
            class C:
                @transaction.atomic()
                def a(self):
                    save()

                def b(self):
                    try:
                        save()
                    except IntegrityError:
                        handle()
        """)

    def test_catch_after_the_atomic_block_closes(self):
        assert not violations("""
            def f():
                with transaction.atomic():
                    save()
                try:
                    save_more()
                except IntegrityError:
                    handle()
        """)

    def test_handler_inside_a_nested_atomic_is_measured_against_it(self):
        """The inner block is its own scope, so a catch inside *it* is still a violation."""
        found = violations("""
            def f():
                with transaction.atomic():
                    try:
                        with transaction.atomic():
                            try:
                                save()
                            except IntegrityError:
                                handle()
                    except IntegrityError:
                        handle()
        """)
        assert len(found) == 1
        assert found[0].atomic_lineno == 5


class TestMain:
    def _write(self, tmp_path: Path, source: str) -> Path:
        path = tmp_path / "mod.py"
        path.write_text(textwrap.dedent(source))
        return path

    def test_exit_code_and_output_on_violation(self, tmp_path, capsys):
        self._write(
            tmp_path,
            """
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        handle()
            """,
        )
        assert main([str(tmp_path)]) == 1
        assert "except IntegrityError" in capsys.readouterr().out

    def test_exit_code_when_clean(self, tmp_path, capsys):
        self._write(tmp_path, "def f():\n    save()\n")
        assert main([str(tmp_path)]) == 0
        assert "No database exceptions" in capsys.readouterr().out

    def test_files_argument_limits_the_scan(self, tmp_path, capsys):
        bad = self._write(
            tmp_path,
            """
            def f():
                with transaction.atomic():
                    try:
                        save()
                    except IntegrityError:
                        handle()
            """,
        )
        clean = tmp_path / "clean.py"
        clean.write_text("def g():\n    pass\n")
        assert main([str(tmp_path), "--files", str(clean)]) == 0
        assert main([str(tmp_path), "--files", str(bad)]) == 1

    def test_missing_and_non_python_files_are_skipped(self, tmp_path):
        assert main([str(tmp_path), "--files", str(tmp_path / "gone.py"), str(tmp_path / "notes.txt")]) == 0

    def test_migrations_are_skipped(self, tmp_path, capsys):
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "0001_initial.py").write_text(
            textwrap.dedent("""
                def f():
                    with transaction.atomic():
                        try:
                            save()
                        except IntegrityError:
                            handle()
            """)
        )
        assert main([str(tmp_path)]) == 0

    def test_unparseable_file_is_reported_but_not_fatal(self, tmp_path, capsys):
        self._write(tmp_path, "def f(:\n")
        assert main([str(tmp_path)]) == 0
        assert "SKIP" in capsys.readouterr().err


def test_the_repo_is_clean():
    """Repo-wide gate: `apps/` must stay free of this pattern.

    The pre-commit hook only sees the files a commit touches, so this test is what keeps the
    whole tree clean (e.g. when a violation arrives via a merge or a file rename).
    """
    assert main([str(Path(__file__).resolve().parent.parent / "apps")]) == 0
