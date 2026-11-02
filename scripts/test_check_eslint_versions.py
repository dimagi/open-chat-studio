"""Tests for check_eslint_versions.py.

Run with: uv run pytest scripts/test_check_eslint_versions.py -v
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import check_eslint_versions  # noqa: E402
from check_eslint_versions import fix_hook_pins, main  # noqa: E402

PRE_COMMIT_CONFIG = textwrap.dedent(
    """\
    repos:
      - repo: https://github.com/pre-commit/mirrors-eslint
        rev: 'v10.11.0'
        hooks:
          - id: eslint
            # Keep these in sync.
            additional_dependencies:
              - eslint@10.11.0
              - '@eslint/js@10.0.1'
              - typescript-eslint@8.70.1  # trailing comment
            args: [ --fix ]
      - repo: https://github.com/rbubley/mirrors-prettier
        rev: 'v3.9.9'
        hooks:
          - id: prettier
            additional_dependencies:
              - eslint@1.0.0
    """
)

LOCKFILE = textwrap.dedent(
    """\
    importers:
      .:
        devDependencies:
          '@eslint/js':
            specifier: ^10.0.1
            version: {js}(eslint@{eslint}(jiti@2.7.0))
          eslint:
            specifier: ^10.11.0
            version: {eslint}(jiti@2.7.0)
          typescript-eslint:
            specifier: ^8.70.1
            version: {ts}(eslint@{eslint}(jiti@2.7.0))
    """
)


def write_lock(path: Path, *, eslint="10.11.0", js="10.0.1", ts="8.70.1", extra=""):
    path.write_text(LOCKFILE.format(eslint=eslint, js=js, ts=ts) + extra)


@pytest.fixture()
def repo_files(tmp_path, monkeypatch):
    config = tmp_path / ".pre-commit-config.yaml"
    lock = tmp_path / "pnpm-lock.yaml"
    config.write_text(PRE_COMMIT_CONFIG)
    write_lock(lock)
    monkeypatch.setattr(check_eslint_versions, "PRE_COMMIT_CONFIG", config)
    monkeypatch.setattr(check_eslint_versions, "PNPM_LOCK", lock)
    return config, lock


class TestFixHookPins:
    def test_in_sync_is_unchanged(self):
        resolved = {"eslint": "10.11.0", "@eslint/js": "10.0.1", "typescript-eslint": "8.70.1"}
        assert fix_hook_pins(PRE_COMMIT_CONFIG, resolved) == PRE_COMMIT_CONFIG

    @pytest.mark.parametrize(
        ("resolved", "expected_lines"),
        [
            pytest.param(
                {"eslint": "10.11.0", "@eslint/js": "10.0.1", "typescript-eslint": "8.72.0"},
                ["- typescript-eslint@8.72.0  # trailing comment", "rev: 'v10.11.0'"],
                id="one-pin-behind",
            ),
            pytest.param(
                {"eslint": "10.13.0", "@eslint/js": "10.1.0", "typescript-eslint": "8.72.0"},
                [
                    "rev: 'v10.13.0'",
                    "- eslint@10.13.0",
                    "- '@eslint/js@10.1.0'",
                    "- typescript-eslint@8.72.0  # trailing comment",
                ],
                id="all-pins-and-rev-behind",
            ),
        ],
    )
    def test_rewrites_pins(self, resolved, expected_lines):
        fixed = fix_hook_pins(PRE_COMMIT_CONFIG, resolved)
        for line in expected_lines:
            assert line in fixed

    def test_preserves_comments_and_other_hooks(self):
        resolved = {"eslint": "10.13.0", "@eslint/js": "10.0.1", "typescript-eslint": "8.70.1"}
        fixed = fix_hook_pins(PRE_COMMIT_CONFIG, resolved)
        assert "# Keep these in sync." in fixed
        assert "rev: 'v3.9.9'" in fixed
        assert "- eslint@1.0.0" in fixed

    def test_pin_missing_from_lockfile_is_left_alone(self):
        resolved = {"eslint": "10.13.0", "typescript-eslint": "8.70.1"}
        fixed = fix_hook_pins(PRE_COMMIT_CONFIG, resolved)
        assert "- '@eslint/js@10.0.1'" in fixed

    @pytest.mark.parametrize(
        ("rev_line", "expected"),
        [
            pytest.param("rev: '10.11.0'", "rev: '10.13.0'", id="no-v-prefix-quoted"),
            pytest.param("rev: 10.11.0", "rev: 10.13.0", id="no-v-prefix-unquoted"),
            pytest.param('rev: "v10.11.0"  # pinned', 'rev: "v10.13.0"  # pinned', id="v-prefix-double-quoted-comment"),
        ],
    )
    def test_rev_formats_keep_their_style(self, rev_line, expected):
        config = PRE_COMMIT_CONFIG.replace("rev: 'v10.11.0'", rev_line, 1)
        resolved = {"eslint": "10.13.0", "@eslint/js": "10.0.1", "typescript-eslint": "8.70.1"}
        assert expected in fix_hook_pins(config, resolved)

    def test_no_eslint_mirror_is_unchanged(self):
        config = "repos:\n  - repo: https://example.com/x\n    rev: v1\n"
        assert fix_hook_pins(config, {"eslint": "10.13.0"}) == config


class TestMain:
    def test_in_sync_passes(self, repo_files, capsys):
        assert main([]) == 0
        assert "in sync" in capsys.readouterr().out

    def test_drift_fails_and_names_fix(self, repo_files, capsys):
        _, lock = repo_files
        write_lock(lock, ts="8.72.0")
        assert main([]) == 1
        out = capsys.readouterr().out
        assert "typescript-eslint: .pre-commit-config.yaml pins 8.70.1 but pnpm-lock.yaml resolves 8.72.0" in out
        assert "--fix" in out

    def test_fix_repairs_drift(self, repo_files, capsys):
        config, lock = repo_files
        write_lock(lock, eslint="10.13.0", ts="8.72.0")
        assert main(["--fix"]) == 0
        text = config.read_text()
        assert "rev: 'v10.13.0'" in text
        assert "- eslint@10.13.0" in text
        assert "- typescript-eslint@8.72.0  # trailing comment" in text
        assert main([]) == 0

    def test_fix_still_fails_when_pin_missing_from_lockfile(self, repo_files, capsys):
        _, lock = repo_files
        lock.write_text(
            LOCKFILE.format(eslint="10.13.0", js="10.0.1", ts="8.72.0").replace("      '@eslint/js':", "      'other':")
        )
        assert main(["--fix"]) == 1
        assert "@eslint/js: pinned at 10.0.1" in capsys.readouterr().out
