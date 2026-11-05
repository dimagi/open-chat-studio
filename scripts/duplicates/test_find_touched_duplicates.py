"""Tests for find_touched_duplicates.py.

Run with: uv run pytest scripts/duplicates/test_find_touched_duplicates.py -v
"""

import json
import subprocess
from pathlib import Path

import pytest

from scripts.duplicates.find_touched_duplicates import (
    WHOLE_FILE,
    Group,
    Region,
    find_touched_groups,
    find_treepeat,
    format_json,
    format_markdown,
    get_touched_lines,
    main,
    parse_diff,
    parse_treepeat_sarif,
    resolve_base,
)

requires_treepeat = pytest.mark.skipif(
    find_treepeat() is None, reason="treepeat is not installed; run `uv sync --group code-analysis`"
)


def diff_for(path: str, *hunk_headers: str) -> str:
    hunks = "\n".join(f"{header}\n+changed" for header in hunk_headers)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{hunks}\n"


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        pytest.param(diff_for("a.py", "@@ -10,2 +10,3 @@"), {"a.py": [(10, 12)]}, id="added-lines"),
        pytest.param(diff_for("a.py", "@@ -10 +10 @@"), {"a.py": [(10, 10)]}, id="count-omitted-means-one"),
        pytest.param(diff_for("a.py", "@@ -10,3 +9,0 @@"), {"a.py": [(9, 10)]}, id="pure-deletion"),
        pytest.param(diff_for("a.py", "@@ -1,3 +0,0 @@"), {"a.py": [(1, 1)]}, id="deletion-at-file-start"),
        pytest.param(
            diff_for("a.py", "@@ -1 +1 @@", "@@ -20,0 +21,4 @@"),
            {"a.py": [(1, 1), (21, 24)]},
            id="several-hunks",
        ),
        pytest.param(
            "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
            "--- a/old.py\n+++ b/new.py\n@@ -5 +5 @@\n-x\n+y\n",
            {"new.py": [(5, 5)]},
            id="renamed-file-uses-new-path",
        ),
        pytest.param(
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-x\n-y\n",
            {},
            id="deleted-file-skipped",
        ),
        pytest.param(
            "diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -1 +1,2 @@\n-x\n+++ b/other.py\n+y\n"
            "@@ -9 +10 @@\n-x\n+y\n",
            {"a.md": [(1, 2), (10, 10)]},
            id="added-line-like-file-header",
        ),
        pytest.param(
            "diff --git a/sp ace.py b/sp ace.py\n--- a/sp ace.py\n+++ b/sp ace.py\t\n@@ -1 +1 @@\n-x\n+y\n",
            {"sp ace.py": [(1, 1)]},
            id="path-with-space-has-trailing-tab",
        ),
    ],
)
def test_parse_diff(diff, expected):
    assert parse_diff(diff) == expected


def sarif_result(similarity_percent: float, regions: list[tuple[str, int, int]]) -> dict:
    return {
        "rule_id": "similar-code",
        "properties": {
            "similarityPercent": similarity_percent,
            "regions": [
                {
                    "path": path,
                    "startLine": start,
                    "endLine": end,
                    "lines": end - start + 1,
                    "type": "function_definition",
                    "name": f"func_{start}",
                }
                for path, start, end in regions
            ],
        },
    }


def test_parse_treepeat_sarif():
    data = {"runs": [{"results": [sarif_result(85.0, [("a.py", 1, 5), ("b.py", 10, 14)])]}]}

    assert parse_treepeat_sarif(data) == [
        Group(
            similarity=85.0,
            regions=[
                Region(path="a.py", start=1, end=5, name="func_1", kind="function_definition"),
                Region(path="b.py", start=10, end=14, name="func_10", kind="function_definition"),
            ],
        )
    ]


A_REGION = Region(path="a.py", start=10, end=20)
B_REGION = Region(path="b.py", start=30, end=40)


@pytest.mark.parametrize(
    ("touched", "expected_touched"),
    [
        pytest.param({"a.py": [(15, 15)]}, [A_REGION], id="line-inside-region"),
        pytest.param({"a.py": [(5, 10)]}, [A_REGION], id="range-overlaps-start"),
        pytest.param({"a.py": [(20, 25)]}, [A_REGION], id="range-overlaps-end"),
        pytest.param({"a.py": [(1, 30)]}, [A_REGION], id="range-covers-region"),
        pytest.param({"a.py": [(1, 9), (21, 30)]}, None, id="ranges-either-side"),
        pytest.param({"c.py": [(15, 15)]}, None, id="other-file"),
        pytest.param({"a.py": [(15, 15)], "b.py": [(35, 35)]}, [A_REGION, B_REGION], id="both-regions-touched"),
        pytest.param({"b.py": [WHOLE_FILE]}, [B_REGION], id="untracked-file"),
    ],
)
def test_find_touched_groups(touched, expected_touched):
    group = Group(similarity=90.0, regions=[A_REGION, B_REGION])

    result = find_touched_groups(groups=[group], touched=touched)

    if expected_touched is None:
        assert result == []
    else:
        assert len(result) == 1
        assert result[0].touched == expected_touched
        assert result[0].others == [r for r in group.regions if r not in expected_touched]


def test_find_touched_groups_orders_by_similarity():
    low = Group(similarity=81.0, regions=[A_REGION, B_REGION])
    high = Group(similarity=99.0, regions=[Region(path="a.py", start=50, end=60), B_REGION])

    result = find_touched_groups(groups=[low, high], touched={"a.py": [(1, 100)]})

    assert [g.group for g in result] == [high, low]


def test_format_markdown():
    touched_groups = find_touched_groups(
        groups=[Group(similarity=92.5, regions=[A_REGION, B_REGION])], touched={"a.py": [(15, 15)]}
    )

    output = format_markdown(touched_groups=touched_groups, similarity=80, min_lines=3)

    assert "1 duplicate group" in output
    assert "92.5% similar" in output
    assert "`a.py:10-20`" in output
    assert "`b.py:30-40`" in output


def test_format_markdown_empty():
    assert "No duplicate code" in format_markdown(touched_groups=[], similarity=80, min_lines=3)


def test_format_json():
    touched_groups = find_touched_groups(
        groups=[Group(similarity=92.5, regions=[A_REGION, B_REGION])], touched={"a.py": [(15, 15)]}
    )

    assert json.loads(format_json(touched_groups)) == [
        {
            "similarity": 92.5,
            "touched": [{"path": "a.py", "start": 10, "end": 20, "name": "", "kind": ""}],
            "others": [{"path": "b.py", "start": 30, "end": 40, "name": "", "kind": ""}],
        }
    ]


DUPLICATED_FUNCTION = """\
def {name}(items):
    total = 0
    for item in items:
        if item.enabled:
            total += item.value * 2
    return total
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    """A git repo with a ``main`` branch and a checked-out ``feature`` branch."""
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "checkout", "-b", "feature")
    return tmp_path


def test_get_touched_lines(repo):
    (repo / "pkg" / "a.py").write_text("x = 1\ny = 20\nz = 3\n")
    git(repo, "commit", "-am", "committed change")
    (repo / "pkg" / "a.py").write_text("x = 10\ny = 20\nz = 3\n")
    (repo / "pkg" / "new.py").write_text("n = 1\n")

    touched = get_touched_lines(base="main", repo_root=repo)

    assert touched == {"pkg/a.py": [(1, 2)], "pkg/new.py": [WHOLE_FILE]}


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(("diff.noprefix", "true"), id="noprefix"),
        pytest.param(("diff.mnemonicPrefix", "true"), id="mnemonic-prefix"),
        pytest.param(("core.quotePath", "true"), id="quote-path"),
    ],
)
def test_get_touched_lines_ignores_user_git_config(repo, config):
    (repo / "pkg" / "café.py").write_text("c = 1\n")
    (repo / "pkg" / "sp ace.py").write_text("s = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "awkward paths")
    git(repo, "branch", "-f", "main")
    git(repo, "config", *config)
    (repo / "pkg" / "a.py").write_text("x = 10\ny = 2\nz = 3\n")
    (repo / "pkg" / "café.py").write_text("c = 10\n")
    (repo / "pkg" / "sp ace.py").write_text("s = 10\n")
    (repo / "pkg" / "naïve.py").write_text("n = 1\n")

    assert get_touched_lines(base="main", repo_root=repo) == {
        "pkg/a.py": [(1, 1)],
        "pkg/café.py": [(1, 1)],
        "pkg/sp ace.py": [(1, 1)],
        "pkg/naïve.py": [WHOLE_FILE],
    }


def test_get_touched_lines_ignores_changes_on_base(repo):
    git(repo, "checkout", "main")
    (repo / "pkg" / "a.py").write_text("x = 1\ny = 2\nz = 30\n")
    git(repo, "commit", "-am", "change on main")
    git(repo, "checkout", "feature")

    assert get_touched_lines(base="main", repo_root=repo) == {}


@requires_treepeat
def test_main_reports_duplicate_of_untouched_code(repo, capsys, monkeypatch):
    (repo / "pkg" / "existing.py").write_text(DUPLICATED_FUNCTION.format(name="existing_total"))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "existing code")
    git(repo, "branch", "-f", "main")
    (repo / "pkg" / "new.py").write_text(DUPLICATED_FUNCTION.format(name="new_total"))
    monkeypatch.chdir(repo)

    exit_code = main(["--base", "main", "--format", "json", "pkg"])

    assert exit_code == 0
    [group] = json.loads(capsys.readouterr().out)
    assert [r["path"] for r in group["touched"]] == ["pkg/new.py"]
    assert [r["path"] for r in group["others"]] == ["pkg/existing.py"]


@requires_treepeat
@pytest.mark.parametrize(
    ("change_function", "expected_exit_code"),
    [
        pytest.param(True, 1, id="duplicate-found"),
        pytest.param(False, 0, id="nothing-found"),
    ],
)
def test_main_fail_flag(repo, monkeypatch, change_function, expected_exit_code):
    (repo / "pkg" / "one.py").write_text(DUPLICATED_FUNCTION.format(name="one"))
    (repo / "pkg" / "two.py").write_text(DUPLICATED_FUNCTION.format(name="two"))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "duplicated code")
    git(repo, "branch", "-f", "main")
    if change_function:
        (repo / "pkg" / "one.py").write_text(DUPLICATED_FUNCTION.format(name="one_renamed"))
    monkeypatch.chdir(repo)

    assert main(["--base", "main", "--fail", "pkg"]) == expected_exit_code


def test_main_reports_missing_base(repo, capsys, monkeypatch):
    monkeypatch.chdir(repo)

    assert main(["--base", "nonexistent", "pkg"]) == 2
    error = capsys.readouterr().err
    assert "nonexistent" in error
    assert "--base <branch>" in error


def test_main_reports_missing_treepeat(repo, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr("scripts.duplicates.find_touched_duplicates.shutil.which", lambda name: None)
    monkeypatch.setattr("scripts.duplicates.find_touched_duplicates.sys.executable", str(repo / "bin" / "python"))

    assert main(["--base", "main", "pkg"]) == 2
    assert "uv sync --group code-analysis" in capsys.readouterr().err


@pytest.mark.parametrize(
    "treepeat_result",
    [
        pytest.param(subprocess.CalledProcessError(returncode=2, cmd=["treepeat"]), id="treepeat-fails"),
        pytest.param(json.JSONDecodeError("Expecting value", "", 0), id="unreadable-sarif"),
        pytest.param({"unexpected": []}, id="unexpected-sarif"),
    ],
)
def test_main_reports_treepeat_failure(repo, capsys, monkeypatch, treepeat_result):
    def fake_run_treepeat(**kwargs):
        if isinstance(treepeat_result, Exception):
            raise treepeat_result
        return treepeat_result

    monkeypatch.chdir(repo)
    monkeypatch.setattr("scripts.duplicates.find_touched_duplicates.run_treepeat", fake_run_treepeat)

    assert main(["--base", "main", "pkg"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "treepeat" in captured.err


@pytest.mark.parametrize(
    ("base", "github_base_ref", "expected"),
    [
        pytest.param("release", "develop", "release", id="explicit-base-wins"),
        pytest.param(None, "develop", "origin/develop", id="github-base-ref"),
        pytest.param(None, None, "origin/main", id="default-main"),
        pytest.param(None, "", "origin/main", id="empty-github-base-ref"),
    ],
)
def test_resolve_base(base, github_base_ref, expected, monkeypatch):
    if github_base_ref is None:
        monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    else:
        monkeypatch.setenv("GITHUB_BASE_REF", github_base_ref)
    assert resolve_base(base) == expected
