"""Tests for check_prerequisite_releases.py.

Run with: uv run pytest scripts/test_check_prerequisite_releases.py -v
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import check_prerequisite_releases  # noqa: E402
from check_prerequisite_releases import (  # noqa: E402
    Prerequisite,
    UnreadableSection,
    find_blockers,
    parse_prerequisites,
    release_tags_containing,
)

TEMPLATE_COMMENT = "<!--\nList merged PRs, one per line as `- #1234`.\n-->"


def body(section: str) -> str:
    """A PR body with the given section, followed by a section listing `- #999` that must be ignored."""
    return f"### Product Description\nno change\n\n### Prerequisite PRs\n{section}\n\n### Demo\n- #999\n"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        pytest.param(None, [], id="no-body"),
        pytest.param("### Product Description\nno change\n", [], id="no-section"),
        pytest.param(body(""), [], id="empty-section"),
        pytest.param(body(TEMPLATE_COMMENT), [], id="only-template-comment"),
        pytest.param(body("None"), [], id="none"),
        pytest.param(body("- none."), [], id="bulleted-none"),
        pytest.param(body("- #12\n- #34"), [12, 34], id="two-prs"),
        pytest.param(body(f"{TEMPLATE_COMMENT}\n- #12"), [12], id="pr-after-comment"),
        pytest.param(body("- #12 (runs the backfill)\n* #34\n#56"), [12, 34, 56], id="bullet-styles-and-notes"),
        pytest.param(body("- #12\n- #12"), [12], id="duplicate"),
        pytest.param("## prerequisite prs\n- #7", [7], id="heading-case-and-level"),
        pytest.param("### Prerequisite PRs:\n- #7", [7], id="heading-with-colon"),
        pytest.param("### Prerequisite PRs\r\n- #7\r\n- #8\r\n", [7, 8], id="crlf"),
        pytest.param("### Prerequisite PRs\n- #7\n\n---\nGenerated with a tool", [7], id="ends-at-rule"),
        pytest.param(
            "Example:\n```\n### Prerequisite PRs\nNone\n```\n\n### Prerequisite PRs\n- #7\n",
            [7],
            id="ignores-heading-in-code-block",
        ),
    ],
)
def test_parse_prerequisites(description, expected):
    assert parse_prerequisites(description) == expected


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("- PR 12", id="no-hash"),
        pytest.param("- #12, #34", id="two-prs-on-one-line"),
        pytest.param("- #12 and #34", id="two-prs-in-prose"),
        pytest.param("- #12.34", id="malformed-number"),
    ],
)
def test_parse_rejects_lines_it_cannot_read(line):
    with pytest.raises(UnreadableSection, match=re.escape(repr(line))):
        parse_prerequisites(body(f"- #1\n{line}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A repo with commits `released`, `in_rc`, `in_widget` and `unreleased`, tagged in that order."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "tag.gpgsign", "false")
    shas = {}
    for name, tag in [("released", "v1.0.0"), ("in_rc", "v1.1.0-rc1"), ("in_widget", "w_v0.1.0"), ("unreleased", None)]:
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", name)
        shas[name] = git(tmp_path, "rev-parse", "HEAD")
        if tag:
            git(tmp_path, "tag", tag)
    monkeypatch.chdir(tmp_path)
    return shas


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        pytest.param("released", ["v1.0.0", "v1.1.0-rc1"], id="in-release"),
        pytest.param("in_rc", ["v1.1.0-rc1"], id="in-pre-release"),
        pytest.param("in_widget", [], id="only-in-widget-tag"),
        pytest.param("unreleased", [], id="untagged"),
    ],
)
def test_release_tags_containing(repo, commit, expected):
    assert release_tags_containing(repo[commit]) == expected


def test_release_tags_containing_an_unknown_commit(repo):
    assert release_tags_containing("0" * 40) is None


def test_find_blockers(repo):
    prerequisites = [
        Prerequisite(number=1, merge_commit_sha=repo["released"]),
        Prerequisite(number=2, merge_commit_sha=repo["in_rc"]),
        Prerequisite(number=3, merge_commit_sha=repo["in_widget"]),
        Prerequisite(number=4, merge_commit_sha=None),
        Prerequisite(number=5, merge_commit_sha="0" * 40),
    ]
    assert find_blockers(prerequisites) == [
        "#3 is merged but not yet in a release",
        "#4 is not merged",
        "#5: its merge commit 000000000000 is not in this clone",
    ]


def fake_pulls(monkeypatch, pulls: dict[int, dict]):
    def gh_api(path):
        number = int(path.rsplit("/", 1)[1])
        if number not in pulls:
            raise RuntimeError("Not Found")
        return pulls[number]

    monkeypatch.setattr(check_prerequisite_releases, "gh_api", gh_api)


def test_main_passes_when_every_prerequisite_is_released(repo, monkeypatch):
    fake_pulls(
        monkeypatch=monkeypatch,
        pulls={100: {"body": body("- #1")}, 1: {"merged": True, "merge_commit_sha": repo["released"]}},
    )
    assert check_prerequisite_releases.main(["--repo", "o/r", "--pr", "100"]) == 0


def test_main_fails_and_names_each_blocking_prerequisite(repo, monkeypatch, capsys):
    fake_pulls(
        monkeypatch=monkeypatch,
        pulls={
            100: {"body": body("- #1\n- #2\n- #3")},
            1: {"merged": True, "merge_commit_sha": repo["released"]},
            2: {"merged": False, "merge_commit_sha": "abc"},
        },
    )
    assert check_prerequisite_releases.main(["--repo", "o/r", "--pr", "100"]) == 1
    out = capsys.readouterr().out
    assert "#1" not in out
    assert "::error::#2 is not merged" in out
    assert "::error::#3 could not be read as a pull request in o/r: Not Found" in out


def test_main_fails_on_an_unreadable_section(monkeypatch, capsys):
    fake_pulls(monkeypatch=monkeypatch, pulls={100: {"body": body("see the other PR")}})
    assert check_prerequisite_releases.main(["--repo", "o/r", "--pr", "100"]) == 1
    assert "not one PR in the form" in capsys.readouterr().out
