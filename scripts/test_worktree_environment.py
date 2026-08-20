"""Tests for the tool-neutral worktree setup and teardown scripts."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "setup-worktree.sh"
TEARDOWN_SCRIPT = REPOSITORY_ROOT / "scripts" / "teardown-worktree.sh"
ENSURE_SETUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "ensure-worktree-setup.sh"
WORKTREE_ENVIRONMENT_SCRIPT = REPOSITORY_ROOT / "scripts" / "worktree-environment.sh"
CODEX_HOOKS_FILE = REPOSITORY_ROOT / ".codex" / "hooks.json"

FAKE_BOOTSTRAP_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$PWD/.venv" "$PWD/node_modules"
printf "bootstrap:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
"""

FAKE_PSQL_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
printf "psql:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
if [[ "${OCS_TEST_FAIL_PSQL:-false}" == "true" ]]; then exit 1; fi
if [[ "$*" == *"SELECT 1 FROM pg_database"* ]]; then exit 0; fi
"""

FAKE_UV_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
printf "uv:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
"""

FAKE_REDIS_CLI_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${*: -1}" == "FLUSHDB" ]]; then
    printf "redis-cli:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
    [[ "${OCS_TEST_FAIL_REDIS_FLUSH:-false}" != "true" ]]
    exit 0
fi

operation=
resource_name=
database=
for argument in "$@"; do
    if [[ "$argument" =~ ^(allocate|lookup|release)$ ]]; then
        operation=$argument
        continue
    fi
    if [[ -n "$operation" && -z "$resource_name" ]]; then
        resource_name=$argument
    elif [[ "$operation" == "release" && -n "$resource_name" ]]; then
        database=$argument
    fi
done

printf "redis-registry:%s:%s:%s\n" "$operation" "$resource_name" "$database" \
    >> "$OCS_TEST_COMMAND_LOG"
touch "$OCS_TEST_REDIS_REGISTRY"
existing=$(awk -v resource="$resource_name" '$1 == resource { print $2 }' \
    "$OCS_TEST_REDIS_REGISTRY")

if [[ "$operation" == "lookup" ]]; then
    [[ -n "$existing" ]] || exit 1
    printf "%s\n" "$existing"
    exit 0
fi

if [[ "$operation" == "allocate" ]]; then
    if [[ -n "$existing" ]]; then printf "%s\n" "$existing"; exit 0; fi
    for candidate in $(seq 1 14); do
        if ! awk -v database="$candidate" \
            '$2 == database { found = 1 } END { exit !found }' \
            "$OCS_TEST_REDIS_REGISTRY"; then
            printf "%s %s\n" "$resource_name" "$candidate" >> "$OCS_TEST_REDIS_REGISTRY"
            printf "%s\n" "$candidate"
            exit 0
        fi
    done
    echo "Redis database registry exhausted" >&2
    exit 1
fi

if [[ "$operation" == "release" ]]; then
    [[ "$existing" == "$database" ]] || exit 1
    awk -v resource="$resource_name" '$1 != resource' "$OCS_TEST_REDIS_REGISTRY" \
        > "$OCS_TEST_REDIS_REGISTRY.tmp"
    mv "$OCS_TEST_REDIS_REGISTRY.tmp" "$OCS_TEST_REDIS_REGISTRY"
    echo 1
    exit 0
fi

exit 1
"""


@dataclass(frozen=True)
class SessionGuardScenario:
    database_name: str
    thread_id: str
    setup_script: str
    expected_log: str | None
    redis_url: str | None = None
    python_version: str | None = None
    migration_path: str | None = None


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


def _run_worktree_helper(
    worktree: Path,
    function_name: str,
    *arguments: str | Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        "bash",
        "-c",
        f'source "$1"; shift; {function_name} "$@"',
        "_",
        worktree / "scripts" / "worktree-environment.sh",
        *arguments,
        cwd=worktree,
        env=env,
        check=check,
    )


def _record_dependency_fingerprint(worktree: Path) -> None:
    _run_worktree_helper(worktree, "ocs_record_dependency_fingerprint", worktree)


def _allocate_redis_database(worktree: Path, env: dict[str, str], resource_name: str) -> int:
    result = _run_worktree_helper(
        worktree,
        "ocs_allocate_redis_database",
        resource_name,
        env=env,
    )
    return int(result.stdout)


def _prepare_session_guard(
    worktree: Path,
    env: dict[str, str],
    scenario: SessionGuardScenario,
) -> Path:
    redis_database = _allocate_redis_database(worktree, env, "codex_a1b2")
    expected_redis_url = scenario.redis_url or f"redis://localhost:6379/{redis_database}"
    (worktree / ".env").write_text(
        "SECRET_KEY=test-only\n"
        f"DATABASE_URL=postgres://postgres:postgres@localhost:5432/{scenario.database_name}\n"
        f"REDIS_URL={expected_redis_url}\n"
    )
    (worktree / ".venv").mkdir()
    (worktree / "node_modules").mkdir()
    _record_dependency_fingerprint(worktree)
    _write_executable(worktree / "scripts" / "setup-worktree.sh", scenario.setup_script)
    command_log = Path(env["OCS_TEST_COMMAND_LOG"])
    env.update(
        {
            "CODEX_THREAD_ID": scenario.thread_id,
            "TMPDIR": str(command_log.parent),
        }
    )
    return command_log.parent / f"ocs-worktree-setup-{scenario.thread_id}.log"


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

    _write_executable(root / "scripts" / "bootstrap.sh", FAKE_BOOTSTRAP_SCRIPT)
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

    _write_executable(fake_bin / "psql", FAKE_PSQL_SCRIPT)
    _write_executable(fake_bin / "uv", FAKE_UV_SCRIPT)
    _write_executable(fake_bin / "redis-cli", FAKE_REDIS_CLI_SCRIPT)

    env = {
        "OCS_TEST_COMMAND_LOG": str(command_log),
        "OCS_TEST_REDIS_REGISTRY": str(tmp_path / "redis-registry"),
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


@pytest.mark.parametrize(
    ("first_identifier", "second_identifier"),
    [
        pytest.param("feature/a", "feature_a", id="sanitization"),
        pytest.param(f"{'x' * 63}a", f"{'x' * 63}b", id="truncation"),
    ],
)
def test_resource_names_do_not_collide_after_sanitization(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    first_identifier: str,
    second_identifier: str,
) -> None:
    _, worktree, _, _ = worktree_fixture

    first_name = _run_worktree_helper(
        worktree,
        "ocs_sanitize_resource_name",
        first_identifier,
    ).stdout.strip()
    second_name = _run_worktree_helper(
        worktree,
        "ocs_sanitize_resource_name",
        second_identifier,
    ).stdout.strip()

    assert first_name != second_name
    assert len(first_name) <= 63
    assert len(second_name) <= 63


def test_redis_registry_allocates_distinct_databases_and_reuses_assignments(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture

    first_database = _allocate_redis_database(worktree, env, "feature_6")
    second_database = _allocate_redis_database(worktree, env, "feature_17")
    repeated_database = _allocate_redis_database(worktree, env, "feature_6")

    assert first_database != second_database
    assert repeated_database == first_database


def test_redis_registry_reports_exhaustion(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture
    for index in range(14):
        _allocate_redis_database(worktree, env, f"feature_{index}")

    result = _run_worktree_helper(
        worktree,
        "ocs_allocate_redis_database",
        "one_too_many",
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "exhausted" in result.stderr.lower()


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
    lookup = _run_worktree_helper(
        worktree,
        "ocs_lookup_redis_database",
        "codex_a1b2",
        env=env,
        check=False,
    )
    assert lookup.returncode != 0


def test_failed_setup_preserves_an_existing_redis_allocation(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture
    existing_database = _allocate_redis_database(worktree, env, "codex_a1b2")
    fake_bin = Path(env["PATH"].split(":", maxsplit=1)[0])
    _write_executable(fake_bin / "psql", "#!/usr/bin/env bash\nexit 1\n")

    result = _run(SETUP_SCRIPT, cwd=worktree, env=env, check=False)

    assert result.returncode != 0
    lookup = _run_worktree_helper(
        worktree,
        "ocs_lookup_redis_database",
        "codex_a1b2",
        env=env,
    )
    assert int(lookup.stdout) == existing_database


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
    redis_database = _allocate_redis_database(worktree, env, "codex_a1b2")

    _run(TEARDOWN_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert 'DROP DATABASE IF EXISTS "codex_a1b2" WITH (FORCE)' in command_output
    assert "redis-cli:-n " in command_output
    assert " FLUSHDB" in command_output
    lookup = _run_worktree_helper(
        worktree,
        "ocs_lookup_redis_database",
        "codex_a1b2",
        env=env,
        check=False,
    )
    assert lookup.returncode != 0
    assert str(redis_database) not in lookup.stdout


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
    _allocate_redis_database(root, env, "feature_database")

    _run(TEARDOWN_SCRIPT, cwd=root, env=env)

    command_output = command_log.read_text()
    assert 'DROP DATABASE IF EXISTS "feature_database" WITH (FORCE)' in command_output
    assert "redis-cli:-n " in command_output


@pytest.mark.parametrize(
    "failure_variable",
    [
        pytest.param("OCS_TEST_FAIL_PSQL", id="postgres"),
        pytest.param("OCS_TEST_FAIL_REDIS_FLUSH", id="redis"),
    ],
)
def test_teardown_propagates_cleanup_failures(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    failure_variable: str,
) -> None:
    _, worktree, env, _ = worktree_fixture
    _allocate_redis_database(worktree, env, "codex_a1b2")
    env[failure_variable] = "true"

    result = _run(TEARDOWN_SCRIPT, cwd=worktree, env=env, check=False)

    assert result.returncode != 0
    assert "Cleaned resources" not in result.stdout
    lookup = _run_worktree_helper(
        worktree,
        "ocs_lookup_redis_database",
        "codex_a1b2",
        env=env,
    )
    assert lookup.stdout.strip().isdigit()


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


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            SessionGuardScenario(
                database_name="codex_a1b2",
                thread_id="initialized-thread",
                setup_script="#!/usr/bin/env bash\nexit 99\n",
                expected_log=None,
            ),
            id="initialized-checkout",
        ),
        pytest.param(
            SessionGuardScenario(
                database_name="codex_a1b2",
                thread_id="stale-dependencies-thread",
                setup_script="#!/usr/bin/env bash\necho 'refreshed dependencies'\n",
                expected_log="refreshed dependencies\n",
                python_version="3.14",
            ),
            id="stale-dependencies",
        ),
        pytest.param(
            SessionGuardScenario(
                database_name="codex_a1b2",
                thread_id="new-migration-thread",
                setup_script="#!/usr/bin/env bash\necho 'applied migrations'\n",
                expected_log="applied migrations\n",
                migration_path="apps/test_app/migrations/0002_new_field.py",
            ),
            id="new-migration",
        ),
        pytest.param(
            SessionGuardScenario(
                database_name="root_database",
                thread_id="shared-database-thread",
                setup_script="#!/usr/bin/env bash\necho 'reconfigured worktree'\n",
                expected_log="reconfigured worktree\n",
            ),
            id="shared-database",
        ),
        pytest.param(
            SessionGuardScenario(
                database_name="codex_a1b2",
                thread_id="shared-redis-thread",
                setup_script="#!/usr/bin/env bash\necho 'reconfigured worktree'\n",
                expected_log="reconfigured worktree\n",
                redis_url="redis://localhost:6379/0",
            ),
            id="shared-redis",
        ),
    ],
)
def test_codex_session_setup_guard(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    scenario: SessionGuardScenario,
) -> None:
    _, worktree, env, _ = worktree_fixture
    log_file = _prepare_session_guard(worktree, env, scenario)
    if scenario.python_version:
        (worktree / ".python-version").write_text(f"{scenario.python_version}\n")
    if scenario.migration_path:
        migration_file = worktree / scenario.migration_path
        migration_file.parent.mkdir(parents=True)
        migration_file.write_text("# test migration\n")

    _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    if scenario.expected_log is None:
        assert not log_file.exists()
    else:
        assert log_file.read_text() == scenario.expected_log


def test_codex_hook_runs_when_a_session_resumes() -> None:
    hooks = json.loads(CODEX_HOOKS_FILE.read_text())

    matcher = hooks["hooks"]["SessionStart"][0]["matcher"]

    assert "startup" in matcher.split("|")
    assert "resume" in matcher.split("|")
