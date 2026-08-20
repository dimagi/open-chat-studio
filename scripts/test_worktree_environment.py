"""Tests for the tool-neutral worktree setup and teardown scripts."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "setup-worktree.sh"
TEARDOWN_SCRIPT = REPOSITORY_ROOT / "scripts" / "teardown-worktree.sh"
ENSURE_SETUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "ensure-worktree-setup.sh"
WORKTREE_ENVIRONMENT_SCRIPT = REPOSITORY_ROOT / "scripts" / "worktree-environment.sh"
CODEX_HOOKS_FILE = REPOSITORY_ROOT / ".codex" / "hooks.json"


def _run(
    *command: str | Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=process_env,
        check=check,
        capture_output=True,
        text=True,
    )


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _record_dependency_fingerprint(worktree: Path) -> None:
    _run(
        "bash",
        "-c",
        'source "$1"; ocs_record_dependency_fingerprint "$2"',
        "_",
        worktree / "scripts" / "worktree-environment.sh",
        worktree,
        cwd=worktree,
    )


@pytest.fixture()
def worktree_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str], Path]:
    root = tmp_path / "main"
    worktree = tmp_path / ".codex" / "worktrees" / "a1b2" / "project"
    fake_bin = tmp_path / "bin"
    command_log = tmp_path / "commands.log"

    root.mkdir()
    _run("git", "init", "--initial-branch=main", cwd=root)
    _run("git", "config", "user.email", "tests@example.com", cwd=root)
    _run("git", "config", "user.name", "Test User", cwd=root)

    _write_executable(
        root / "scripts" / "bootstrap.sh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mkdir -p "$PWD/.venv" "$PWD/node_modules"\n'
        'printf "bootstrap:%s\\n" "$*" >> "$OCS_TEST_COMMAND_LOG"\n',
    )
    (root / "scripts" / "worktree-environment.sh").write_text(WORKTREE_ENVIRONMENT_SCRIPT.read_text())
    (root / ".python-version").write_text("3.13\n")
    (root / "manage.py").write_text("# test fixture\n")
    _run(
        "git",
        "add",
        "scripts/bootstrap.sh",
        "scripts/worktree-environment.sh",
        ".python-version",
        "manage.py",
        cwd=root,
    )
    _run("git", "commit", "-m", "test fixture", cwd=root)

    (root / ".env").write_text(
        "SECRET_KEY=test-only\n"
        "DATABASE_URL=postgres://postgres:postgres@localhost:5432/root_database\n"
        "REDIS_URL=redis://localhost:6379/0\n"
    )
    (root / ".envrc").write_text("export TEST_ONLY=1\n")
    worktree.parent.mkdir(parents=True)
    _run("git", "worktree", "add", "--detach", worktree, "HEAD", cwd=root)

    _write_executable(
        fake_bin / "psql",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "psql:%s\\n" "$*" >> "$OCS_TEST_COMMAND_LOG"\n'
        'if [[ "$*" == *"SELECT 1 FROM pg_database"* ]]; then exit 0; fi\n',
    )
    _write_executable(
        fake_bin / "uv",
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "uv:%s\\n" "$*" >> "$OCS_TEST_COMMAND_LOG"\n',
    )
    _write_executable(
        fake_bin / "redis-cli",
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "redis-cli:%s\\n" "$*" >> "$OCS_TEST_COMMAND_LOG"\n',
    )

    env = {
        "OCS_TEST_COMMAND_LOG": str(command_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    return root, worktree, env, command_log


def test_setup_configures_a_detached_codex_worktree(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    root, worktree, env, command_log = worktree_fixture

    result = _run(SETUP_SCRIPT, cwd=worktree, env=env)

    worktree_env = (worktree / ".env").read_text()
    assert "SECRET_KEY=test-only" in worktree_env
    assert "DATABASE_URL=postgres://postgres:postgres@localhost:5432/codex_a1b2" in worktree_env
    assert "REDIS_URL=redis://localhost:6379/" in worktree_env
    assert (worktree / ".envrc").read_text() == "export TEST_ONLY=1\n"
    assert (root / ".env").read_text().endswith("REDIS_URL=redis://localhost:6379/0\n")

    command_output = command_log.read_text()
    assert "bootstrap:--force --yes" in command_output
    assert 'CREATE DATABASE "codex_a1b2"' in command_output
    assert "uv:run python manage.py migrate" in command_output
    assert (
        "uv:run python manage.py bootstrap_data --email test@example.com --password letmein --superuser"
        in command_output
    )
    assert "Setup complete" in result.stdout


def test_setup_uses_an_explicit_worktrunk_identifier(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    env["OCS_WORKTREE_ID"] = "feature_database"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    assert "5432/feature_database" in (worktree / ".env").read_text()
    assert 'CREATE DATABASE "feature_database"' in command_log.read_text()


def test_setup_runs_from_the_worktree_root_when_started_in_a_subdirectory(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture
    nested_directory = worktree / "apps"
    nested_directory.mkdir()
    env["OCS_EXPECTED_CWD"] = str(worktree)
    _write_executable(
        worktree / "scripts" / "bootstrap.sh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '[[ "$PWD" == "$OCS_EXPECTED_CWD" ]]\n'
        'mkdir -p "$PWD/.venv" "$PWD/node_modules"\n',
    )

    _run(SETUP_SCRIPT, cwd=nested_directory, env=env)


def test_setup_overrides_inherited_service_urls(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    fake_bin = Path(env["PATH"].split(":", maxsplit=1)[0])
    env.update(
        {
            "DATABASE_URL": "postgres://postgres:postgres@localhost:5432/root_database",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "uv-env:%s:%s\\n" "$DATABASE_URL" "$REDIS_URL" >> "$OCS_TEST_COMMAND_LOG"\n',
    )

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    worktree_env = (worktree / ".env").read_text().splitlines()
    database_url = next(line.removeprefix("DATABASE_URL=") for line in worktree_env if line.startswith("DATABASE_URL="))
    redis_url = next(line.removeprefix("REDIS_URL=") for line in worktree_env if line.startswith("REDIS_URL="))
    assert f"uv-env:{database_url}:{redis_url}" in command_log.read_text()


def test_setup_records_dependencies_only_after_services_are_ready(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture
    fake_bin = Path(env["PATH"].split(":", maxsplit=1)[0])
    _write_executable(fake_bin / "psql", "#!/usr/bin/env bash\nexit 1\n")

    result = _run(SETUP_SCRIPT, cwd=worktree, env=env, check=False)

    assert result.returncode != 0
    assert not (worktree / ".venv" / ".ocs-dependency-fingerprint").exists()


def test_setup_does_not_reconfigure_the_root_checkout(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    root, _, env, command_log = worktree_fixture
    original_env = (root / ".env").read_text()

    _run(SETUP_SCRIPT, cwd=root, env=env)

    assert (root / ".env").read_text() == original_env
    assert command_log.read_text() == "bootstrap:--force --yes\n"


def test_teardown_removes_only_the_worktree_resources(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture

    _run(TEARDOWN_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert 'DROP DATABASE IF EXISTS "codex_a1b2" WITH (FORCE)' in command_output
    assert "redis-cli:-n " in command_output
    assert " FLUSHDB" in command_output


def test_teardown_refuses_to_clean_the_root_checkout(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    root, _, env, command_log = worktree_fixture

    result = _run(TEARDOWN_SCRIPT, cwd=root, env=env)

    assert not command_log.exists()
    assert "root checkout" in result.stdout


def test_teardown_allows_worktrunk_to_clean_after_removal(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    root, _, env, command_log = worktree_fixture
    env.update(
        {
            "OCS_ALLOW_ROOT_TEARDOWN": "true",
            "OCS_WORKTREE_ID": "feature_database",
        }
    )

    _run(TEARDOWN_SCRIPT, cwd=root, env=env)

    command_output = command_log.read_text()
    assert 'DROP DATABASE IF EXISTS "feature_database" WITH (FORCE)' in command_output
    assert "redis-cli:-n " in command_output


def test_root_teardown_requires_an_explicit_worktree_identifier(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    root, _, env, command_log = worktree_fixture
    env["OCS_ALLOW_ROOT_TEARDOWN"] = "true"

    result = _run(TEARDOWN_SCRIPT, cwd=root, env=env, check=False)

    assert result.returncode == 1
    assert not command_log.exists()
    assert "OCS_WORKTREE_ID" in result.stderr


def test_codex_session_setup_keeps_bootstrap_output_out_of_context(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_executable(
        worktree / "scripts" / "setup-worktree.sh",
        "#!/usr/bin/env bash\necho 'verbose setup output'\n",
    )
    env.update(
        {
            "CODEX_THREAD_ID": "test-thread",
            "TMPDIR": str(command_log.parent),
        }
    )

    result = _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    assert result.stdout == ""
    assert (command_log.parent / "ocs-worktree-setup-test-thread.log").read_text() == "verbose setup output\n"


def test_codex_session_setup_skips_an_initialized_checkout(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    (worktree / ".env").write_text(
        "SECRET_KEY=test-only\nDATABASE_URL=postgres://postgres:postgres@localhost:5432/codex_a1b2\n"
    )
    (worktree / ".venv").mkdir()
    (worktree / "node_modules").mkdir()
    _record_dependency_fingerprint(worktree)
    _write_executable(
        worktree / "scripts" / "setup-worktree.sh",
        "#!/usr/bin/env bash\nexit 99\n",
    )
    env.update(
        {
            "CODEX_THREAD_ID": "initialized-thread",
            "TMPDIR": str(command_log.parent),
        }
    )

    _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    assert not (command_log.parent / "ocs-worktree-setup-initialized-thread.log").exists()


def test_codex_session_setup_refreshes_stale_dependencies(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    (worktree / ".env").write_text(
        "SECRET_KEY=test-only\nDATABASE_URL=postgres://postgres:postgres@localhost:5432/codex_a1b2\n"
    )
    (worktree / ".venv").mkdir()
    (worktree / "node_modules").mkdir()
    _record_dependency_fingerprint(worktree)
    (worktree / ".python-version").write_text("3.14\n")
    _write_executable(
        worktree / "scripts" / "setup-worktree.sh",
        "#!/usr/bin/env bash\necho 'refreshed dependencies'\n",
    )
    env.update(
        {
            "CODEX_THREAD_ID": "stale-dependencies-thread",
            "TMPDIR": str(command_log.parent),
        }
    )

    _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    assert (command_log.parent / "ocs-worktree-setup-stale-dependencies-thread.log").read_text() == (
        "refreshed dependencies\n"
    )


def test_codex_session_setup_repairs_a_shared_database_configuration(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    (worktree / ".env").write_text(
        "SECRET_KEY=test-only\nDATABASE_URL=postgres://postgres:postgres@localhost:5432/root_database\n"
    )
    (worktree / ".venv").mkdir()
    (worktree / "node_modules").mkdir()
    _write_executable(
        worktree / "scripts" / "setup-worktree.sh",
        "#!/usr/bin/env bash\necho 'reconfigured worktree'\n",
    )
    env.update(
        {
            "CODEX_THREAD_ID": "shared-database-thread",
            "TMPDIR": str(command_log.parent),
        }
    )

    _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    assert (command_log.parent / "ocs-worktree-setup-shared-database-thread.log").read_text() == (
        "reconfigured worktree\n"
    )


def test_codex_hook_runs_when_a_session_resumes() -> None:
    hooks = json.loads(CODEX_HOOKS_FILE.read_text())

    matcher = hooks["hooks"]["SessionStart"][0]["matcher"]

    assert "startup" in matcher.split("|")
    assert "resume" in matcher.split("|")
