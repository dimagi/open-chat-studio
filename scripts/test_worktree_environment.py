"""Tests for the tool-neutral worktree setup and teardown scripts."""

from __future__ import annotations

import json
import os
import shutil
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


# Enough of psql to answer "does this database exist?" and to remember the answer
# changing, which is what picks between building a worktree database from scratch and
# copying a template.


STALE = "ocs_stale_postgres"
ENV_NAME = "ocs_env_postgres"
ENV_LINE = f"OCS_POSTGRES_CONTAINER={ENV_NAME}"
ENV_QUOTED = f"OCS_POSTGRES_CONTAINER='{ENV_NAME}'"
PSQL_QUERY_LOG_LINE = "psql:-h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 -c SELECT 1"


# Where `ocs_psql` is told to find its container, and what should come of it.
@dataclass(frozen=True)
class ContainerSourceScenario:
    exported: str | None = None
    env_file: str | None = None
    listed: bool = True
    expect: str | None = "ocs_test_postgres"
    absent: str | None = None
    stderr_fragment: str | None = None


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


FAKE_BOOTSTRAP_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$PWD/.venv" "$PWD/node_modules"
printf "bootstrap:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
"""

# Enough of psql to answer "does this database exist?" and to remember the answer
# changing, which is what picks between building a worktree database from scratch and
# copying a template.

FAKE_PSQL_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
printf "psql:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
# Whether it runs in the container or on the host, the client needs its credential.
: "${PGPASSWORD:?psql was invoked without PGPASSWORD}"
if [[ "${OCS_TEST_FAIL_PSQL:-false}" == "true" ]]; then exit 1; fi

registry="$OCS_TEST_DATABASE_REGISTRY"
statement="${*: -1}"
touch "$registry"

if [[ "$statement" == *"SELECT 1 FROM pg_database"* ]]; then
    database=$(sed -E "s/.*datname='([^']*)'.*/\1/" <<< "$statement")
    grep -Fxq "$database" "$registry" && echo 1
    exit 0
fi

if [[ "$statement" == *"FROM pg_database"* ]]; then
    # Newest first, matching the real ordering by object id.
    grep '^ocs_tmpl_' "$registry" | tac || true
    exit 0
fi

if [[ "$statement" == *"FROM users_customuser"* ]]; then
    printf '%s\n' "${OCS_TEST_USER_ROW_COUNT:-1}"
    exit 0
fi

if [[ "$statement" == CREATE\ DATABASE* ]]; then
    if [[ -n "${OCS_TEST_REFUSE_TEMPLATE:-}" \
        && "$statement" == *"TEMPLATE \"$OCS_TEST_REFUSE_TEMPLATE\""* ]]; then
        echo "source database \"$OCS_TEST_REFUSE_TEMPLATE\" does not exist" >&2
        exit 1
    fi
    database=$(sed -E 's/^CREATE DATABASE "([^"]*)".*/\1/' <<< "$statement")
    grep -Fxq "$database" "$registry" || printf '%s\n' "$database" >> "$registry"
    exit 0
fi

if [[ "$statement" == DROP\ DATABASE* ]]; then
    database=$(sed -E 's/.*IF EXISTS "([^"]*)".*/\1/' <<< "$statement")
    grep -Fvx "$database" "$registry" > "$registry.tmp" || true
    mv "$registry.tmp" "$registry"
    exit 0
fi
"""

FAKE_UV_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
printf "uv:%s\n" "$*" >> "$OCS_TEST_COMMAND_LOG"
"""

FAKE_DOCKER_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail
# `docker ps` lists the stand-in service containers with the port mappings the helpers
# match on, minus whichever one a test blanked out to exercise the host fallback;
# `docker exec <container> <client> ...` hands the rest of the arguments to the client
# fake on PATH.
if [[ "${1:-}" == "ps" ]]; then
    printf "docker-ps\n" >> "$OCS_TEST_COMMAND_LOG"
    redis_container="${OCS_TEST_REDIS_CONTAINER-ocs_test_redis}"
    postgres_container="${OCS_TEST_POSTGRES_CONTAINER-ocs_test_postgres}"
    [[ -z "$postgres_container" ]] \
        || printf '%s 0.0.0.0:5432->5432/tcp\n' "$postgres_container"
    [[ -z "$redis_container" ]] \
        || printf '%s 0.0.0.0:6379->6379/tcp\n' "$redis_container"
    exit 0
fi

if [[ "${1:-}" == "exec" ]]; then
    shift
    while [[ "${1:-}" == -* ]]; do
        # The environment a real `docker exec -e` would hand the client, kept so the
        # fakes see what the real commands would.
        if [[ "$1" == "-e" ]]; then
            export "${2?docker exec -e needs a value}"
            shift 2
            continue
        fi
        shift
    done
    printf "docker-exec:%s\n" "$1" >> "$OCS_TEST_COMMAND_LOG"
    # The statuses `docker exec` keeps for itself when it cannot start the client at all:
    # 125 for a container it cannot use, 126 and 127 for the executable inside it.
    # Unset, the failure covers every container; set, only the one it names, which is how
    # a test leaves a working container to be discovered behind a stale override.
    if [[ -n "${OCS_TEST_DOCKER_EXEC_FAILURE:-}" \
        && "${OCS_TEST_DOCKER_EXEC_FAILURE_CONTAINER:-$1}" == "$1" ]]; then
        echo "Error: could not exec in $1" >&2
        exit "$OCS_TEST_DOCKER_EXEC_FAILURE"
    fi
    shift
    exec "$@"
fi

exit 1
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
    if [[ "${OCS_TEST_FAIL_REDIS_LOOKUP:-false}" == "true" ]]; then
        echo "Redis registry unavailable" >&2
        exit 2
    fi
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


def _write_migration(worktree: Path, name: str, contents: str = "# migration\n") -> Path:
    migration = worktree / "apps" / "example" / "migrations" / name
    migration.parent.mkdir(parents=True, exist_ok=True)
    migration.write_text(contents)
    return migration


def _template_name(worktree: Path, env: dict[str, str]) -> str:
    return _run_worktree_helper(worktree, "ocs_template_name", worktree, env=env).stdout.strip()


def _created_databases(command_log: Path) -> list[str]:
    return [
        line.split("-c ", maxsplit=1)[1]
        for line in command_log.read_text().splitlines()
        if "-c CREATE DATABASE" in line
    ]


@pytest.fixture()
def worktree_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, str], Path]:
    # Loading the Django settings reads the developer's own .env, so running these
    # tests from a worktree otherwise leaks that worktree's resource names into every
    # script the fixture starts.
    # PGPASSWORD joins them because the psql fake asserts on it: inheriting one from the
    # developer's shell would satisfy that guard no matter what the helper passes.
    for inherited_variable in ("OCS_WORKTREE_ID", "DATABASE_URL", "REDIS_URL", "PGPASSWORD"):
        monkeypatch.delenv(inherited_variable, raising=False)

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
    _write_executable(fake_bin / "docker", FAKE_DOCKER_SCRIPT)

    env = {
        "OCS_TEST_COMMAND_LOG": str(command_log),
        "OCS_TEST_REDIS_REGISTRY": str(tmp_path / "redis-registry"),
        "OCS_TEST_DATABASE_REGISTRY": str(tmp_path / "database-registry"),
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


def test_agent_guard_reuses_the_worktrunk_resource_name(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    env["OCS_WORKTREE_ID"] = "feature_database"
    _run(SETUP_SCRIPT, cwd=worktree, env=env)
    env.pop("OCS_WORKTREE_ID")
    _write_executable(
        worktree / "scripts" / "setup-worktree.sh",
        "#!/usr/bin/env bash\nexit 99\n",
    )
    env.update(
        {
            "CODEX_THREAD_ID": "worktrunk-resource-thread",
            "TMPDIR": str(command_log.parent),
        }
    )

    _run(ENSURE_SETUP_SCRIPT, cwd=worktree, env=env)

    assert "OCS_WORKTREE_ID=feature_database" in (worktree / ".env").read_text()
    assert not (command_log.parent / "ocs-worktree-setup-worktrunk-resource-thread.log").exists()


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


# Every place `ocs_psql` can be told which container to use, and what should come of it.
# The name-shaped check on the last case matters because the value is interpolated into
# `docker exec` and `.env` is a file anything can append to.
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(ContainerSourceScenario(), id="discovered_by_published_port"),
        pytest.param(ContainerSourceScenario(listed=False, expect=None), id="no_container_so_host_client"),
        pytest.param(ContainerSourceScenario(env_file=ENV_LINE, expect=ENV_NAME), id="named_in_the_env_file"),
        pytest.param(ContainerSourceScenario(env_file=ENV_QUOTED, expect=ENV_NAME), id="named_in_the_env_file_quoted"),
        pytest.param(
            ContainerSourceScenario(env_file=ENV_LINE, exported="ocs_exported", expect="ocs_exported", absent=ENV_NAME),
            id="environment_beats_the_env_file",
        ),
        pytest.param(
            ContainerSourceScenario(exported="nope; rm -rf /", stderr_fragment="is not a container name"),
            id="a_value_that_is_not_a_container_name",
        ),
    ],
)
def test_where_psql_finds_its_container(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    scenario: ContainerSourceScenario,
) -> None:
    _, worktree, env, command_log = worktree_fixture
    # Nothing sources `.env` before these scripts run, so a knob set there only works
    # because they read the file themselves — the way OCS_WORKTREE_ID always has.
    if scenario.env_file is not None:
        with (worktree / ".env").open("a") as env_file:
            env_file.write(f"{scenario.env_file}\n")
    if scenario.exported is not None:
        env["OCS_POSTGRES_CONTAINER"] = scenario.exported
    if not scenario.listed:
        env["OCS_TEST_POSTGRES_CONTAINER"] = ""

    # check=True throughout: whichever client answers, the query has to succeed.
    result = _run_worktree_helper(worktree, "ocs_psql", "postgres", "-c", "SELECT 1", env=env)

    command_output = command_log.read_text()
    assert PSQL_QUERY_LOG_LINE in command_output
    if scenario.expect is None:
        assert "docker-exec:" not in command_output
    else:
        assert f"docker-exec:{scenario.expect}" in command_output
    if scenario.absent is not None:
        assert scenario.absent not in command_output
    if scenario.stderr_fragment is not None:
        assert scenario.stderr_fragment in result.stderr


@pytest.mark.parametrize(
    ("listed", "expect_container"),
    [
        pytest.param(True, True, id="in_the_container"),
        pytest.param(False, False, id="host_client_without_a_container"),
    ],
)
def test_where_the_redis_registry_finds_its_client(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    listed: bool,
    expect_container: bool,
) -> None:
    _, worktree, env, command_log = worktree_fixture
    if not listed:
        env["OCS_TEST_REDIS_CONTAINER"] = ""

    _allocate_redis_database(worktree, env, "feature_6")

    command_output = command_log.read_text()
    assert "redis-registry:allocate:feature_6:" in command_output
    assert ("docker-exec:ocs_test_redis" in command_output) is expect_container


# `docker exec` keeps 125/126/127 for itself: a container it cannot use, or no such
# executable inside it. Asserting the exact sequence of attempts covers both what gets
# tried and what does not get tried twice.
# `docker exec` keeps 125/126/127 for itself: a container it cannot use, or no such
# executable inside it. The exact sequence of attempts covers both what gets tried and
# what is not tried twice — a stale override gives way to discovery, and an override that
# stayed eligible after being rejected would come back forever.
@pytest.mark.parametrize(
    ("status", "failing_container", "override", "expected_attempts"),
    [
        pytest.param("125", None, None, ["ocs_test_postgres"], id="container_unusable"),
        pytest.param("127", None, None, ["ocs_test_postgres"], id="no_client_inside_it"),
        pytest.param("125", STALE, STALE, [STALE, "ocs_test_postgres"], id="a_stale_override_gives_way"),
        pytest.param("127", None, STALE, [STALE, "ocs_test_postgres"], id="each_source_tried_once_when_none_can"),
    ],
)
def test_psql_moves_on_when_a_container_cannot_run_a_client(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    status: str,
    failing_container: str | None,
    override: str | None,
    expected_attempts: list[str],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    env["OCS_TEST_DOCKER_EXEC_FAILURE"] = status
    if failing_container is not None:
        env["OCS_TEST_DOCKER_EXEC_FAILURE_CONTAINER"] = failing_container
    if override is not None:
        env["OCS_POSTGRES_CONTAINER"] = override

    # check=True: whichever client ends up answering, the query has to succeed.
    _run_worktree_helper(worktree, "ocs_psql", "postgres", "-c", "SELECT 1", env=env)

    command_output = command_log.read_text()
    attempts = [line for line in command_output.splitlines() if line.startswith("docker-exec:")]
    assert attempts == [f"docker-exec:{name}" for name in expected_attempts]
    assert PSQL_QUERY_LOG_LINE in command_output


def test_the_redis_registry_moves_on_when_the_container_cannot_run_a_client(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    env["OCS_TEST_DOCKER_EXEC_FAILURE"] = "127"

    _allocate_redis_database(worktree, env, "feature_6")

    command_output = command_log.read_text()
    assert "docker-exec:ocs_test_redis" in command_output
    assert "redis-registry:allocate:feature_6:" in command_output


def test_psql_does_not_retry_a_client_that_ran_and_failed(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    # A statement the server rejects is the client's own failure, not the container's:
    # running it again against whatever else answers on the host port would send the same
    # statement to a second server.
    env["OCS_TEST_FAIL_PSQL"] = "true"

    result = _run_worktree_helper(worktree, "ocs_psql", "postgres", "-c", "SELECT 1", env=env, check=False)

    assert result.returncode == 1
    assert command_log.read_text().count("psql:") == 1


def test_service_container_is_resolved_once_per_shell(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture

    # Every query would otherwise cost its own `docker ps`, and a single setup runs
    # dozens of them.
    _run(
        "bash",
        "-c",
        'source "$1"; ocs_psql postgres -c "SELECT 1"; ocs_psql postgres -c "SELECT 2"',
        "_",
        worktree / "scripts" / "worktree-environment.sh",
        cwd=worktree,
        env=env,
    )

    command_output = command_log.read_text()
    assert command_output.count("psql:") == 2
    assert command_output.count("docker-ps") == 1


@pytest.mark.parametrize(
    ("helper", "arguments"),
    [
        pytest.param("ocs_redis_cli", ("PING",), id="redis"),
        pytest.param("ocs_psql", ("postgres", "-c", "SELECT 1"), id="postgres"),
    ],
)
def test_service_clients_report_when_no_client_is_reachable(
    tmp_path: Path,
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
    helper: str,
    arguments: tuple[str, ...],
) -> None:
    _, worktree, env, _ = worktree_fixture
    # A PATH holding neither a container nor a client -- only the shell that runs the
    # helper -- so the helper has to say so rather than fail on a missing command.
    bare_bin = tmp_path / "bare-bin"
    bare_bin.mkdir()
    (bare_bin / "bash").symlink_to(shutil.which("bash") or "/bin/bash")
    env["PATH"] = str(bare_bin)

    result = _run_worktree_helper(worktree, helper, *arguments, env=env, check=False)

    assert result.returncode != 0
    assert "docker-compose-dev.yml" in result.stderr


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


def test_setup_copies_a_matching_template_instead_of_rebuilding(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_migration(worktree, "0001_initial.py")
    _run(SETUP_SCRIPT, cwd=worktree, env=env)
    template_name = _template_name(worktree, env)
    command_log.write_text("")
    env["OCS_WORKTREE_ID"] = "second_worktree"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert f'CREATE DATABASE "second_worktree" TEMPLATE "{template_name}"' in _created_databases(command_log)
    # Migrated even though the template matched exactly: the stamp says nothing about
    # migrations shipped by the packages in `uv.lock`.
    assert "uv:run python manage.py migrate" in command_output
    assert "bootstrap_data" not in command_output


def test_setup_migrates_forward_from_an_ancestor_template(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_migration(worktree, "0001_initial.py")
    _run(SETUP_SCRIPT, cwd=worktree, env=env)
    ancestor_template = _template_name(worktree, env)
    _write_migration(worktree, "0002_later.py")
    command_log.write_text("")
    env["OCS_WORKTREE_ID"] = "second_worktree"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert f'CREATE DATABASE "second_worktree" TEMPLATE "{ancestor_template}"' in _created_databases(command_log)
    assert "uv:run python manage.py migrate" in command_output
    assert "bootstrap_data" not in command_output
    # The migrated result becomes the template for the newer migration set.
    assert f'CREATE DATABASE "{_template_name(worktree, env)}" TEMPLATE "second_worktree"' in _created_databases(
        command_log
    )


def test_setup_ignores_a_template_holding_unknown_migrations(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_migration(worktree, "0001_initial.py")
    later_migration = _write_migration(worktree, "0002_later.py")
    _run(SETUP_SCRIPT, cwd=worktree, env=env)
    later_migration.unlink()
    command_log.write_text("")
    env["OCS_WORKTREE_ID"] = "second_worktree"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert 'CREATE DATABASE "second_worktree"' in _created_databases(command_log)
    assert "bootstrap_data" in command_output


def test_setup_builds_from_scratch_when_templates_are_disabled(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_migration(worktree, "0001_initial.py")
    env["OCS_DISABLE_DATABASE_TEMPLATES"] = "true"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert 'CREATE DATABASE "codex_a1b2"' in _created_databases(command_log)
    assert "bootstrap_data" in command_output
    assert "ocs_tmpl_" not in command_output


def test_setup_seeds_an_existing_database_that_was_never_seeded(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    # A setup that died between creating the database and finishing the seed leaves an
    # empty database that every later run would otherwise only migrate.
    _, worktree, env, command_log = worktree_fixture
    Path(env["OCS_TEST_DATABASE_REGISTRY"]).write_text("codex_a1b2\n")
    env["OCS_TEST_USER_ROW_COUNT"] = "0"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert _created_databases(command_log) == []
    assert "uv:run python manage.py migrate" in command_output
    assert "bootstrap_data" in command_output


def test_setup_does_not_reseed_an_existing_database(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    Path(env["OCS_TEST_DATABASE_REGISTRY"]).write_text("codex_a1b2\n")

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_output = command_log.read_text()
    assert "uv:run python manage.py migrate" in command_output
    assert "bootstrap_data" not in command_output


def test_setup_builds_from_scratch_when_the_template_copy_fails(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    _write_migration(worktree, "0001_initial.py")
    _run(SETUP_SCRIPT, cwd=worktree, env=env)
    template_name = _template_name(worktree, env)
    # The template vanishes the way a concurrent worktree's prune would remove it.
    env["OCS_TEST_REFUSE_TEMPLATE"] = template_name
    env["OCS_TEMPLATE_COPY_RETRY_DELAY"] = "0"
    command_log.write_text("")
    env["OCS_WORKTREE_ID"] = "second_worktree"

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    created = _created_databases(command_log)
    command_output = command_log.read_text()
    assert f'CREATE DATABASE "second_worktree" TEMPLATE "{template_name}"' in created
    assert 'CREATE DATABASE "second_worktree"' in created
    assert "uv:run python manage.py migrate" in command_output
    assert "bootstrap_data" in command_output


def test_setup_creates_the_database_before_allocating_redis(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture

    _run(SETUP_SCRIPT, cwd=worktree, env=env)

    command_lines = command_log.read_text().splitlines()
    create_database = next(index for index, line in enumerate(command_lines) if "-c CREATE DATABASE" in line)
    allocate_redis = next(
        index for index, line in enumerate(command_lines) if line.startswith("redis-registry:allocate")
    )
    assert create_database < allocate_redis


def test_pruning_keeps_the_retained_template_and_the_newest_few(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, command_log = worktree_fixture
    stamp_directory = Path(
        _run_worktree_helper(worktree, "ocs_template_stamp_directory", worktree, env=env).stdout.strip()
    )
    stamp_directory.mkdir(parents=True, exist_ok=True)
    database_registry = Path(env["OCS_TEST_DATABASE_REGISTRY"])
    templates = [f"ocs_tmpl_0000000{index}" for index in range(4)]
    database_registry.write_text("".join(f"{template}\n" for template in templates))
    for template in templates:
        (stamp_directory / f"{template}.stamp").write_text("apps/example/migrations/0001_initial.py:1 2\n")
    env["OCS_TEMPLATE_RETENTION"] = "1"

    _run_worktree_helper(
        worktree,
        "ocs_prune_template_databases",
        worktree,
        templates[0],
        env=env,
    )

    command_output = command_log.read_text()
    # The retained template plus the newest one survive; the rest go, stamps and all.
    assert f'DROP DATABASE IF EXISTS "{templates[0]}"' not in command_output
    assert f'DROP DATABASE IF EXISTS "{templates[3]}"' not in command_output
    assert f'DROP DATABASE IF EXISTS "{templates[1]}"' in command_output
    assert f'DROP DATABASE IF EXISTS "{templates[2]}"' in command_output
    assert not (stamp_directory / f"{templates[1]}.stamp").exists()
    assert (stamp_directory / f"{templates[0]}.stamp").exists()


def test_pruning_does_not_spend_a_retention_slot_on_the_retained_template(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    """The retained template is usually the newest one, having just been snapshotted."""
    _, worktree, env, command_log = worktree_fixture
    stamp_directory = Path(
        _run_worktree_helper(worktree, "ocs_template_stamp_directory", worktree, env=env).stdout.strip()
    )
    stamp_directory.mkdir(parents=True, exist_ok=True)
    database_registry = Path(env["OCS_TEST_DATABASE_REGISTRY"])
    templates = [f"ocs_tmpl_0000000{index}" for index in range(3)]
    database_registry.write_text("".join(f"{template}\n" for template in templates))
    for template in templates:
        (stamp_directory / f"{template}.stamp").write_text("apps/example/migrations/0001_initial.py:1 2\n")
    env["OCS_TEMPLATE_RETENTION"] = "1"

    _run_worktree_helper(
        worktree,
        "ocs_prune_template_databases",
        worktree,
        templates[-1],
        env=env,
    )

    command_output = command_log.read_text()
    # The retained newest template, plus one older one on the retention slot it did not take.
    assert f'DROP DATABASE IF EXISTS "{templates[2]}"' not in command_output
    assert f'DROP DATABASE IF EXISTS "{templates[1]}"' not in command_output
    assert f'DROP DATABASE IF EXISTS "{templates[0]}"' in command_output


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


def test_teardown_propagates_redis_registry_lookup_failures(
    worktree_fixture: tuple[Path, Path, dict[str, str], Path],
) -> None:
    _, worktree, env, _ = worktree_fixture
    redis_database = _allocate_redis_database(worktree, env, "codex_a1b2")
    env["OCS_TEST_FAIL_REDIS_LOOKUP"] = "true"

    result = _run(TEARDOWN_SCRIPT, cwd=worktree, env=env, check=False)

    assert result.returncode == 2
    assert "Cleaned resources" not in result.stdout
    env.pop("OCS_TEST_FAIL_REDIS_LOOKUP")
    lookup = _run_worktree_helper(
        worktree,
        "ocs_lookup_redis_database",
        "codex_a1b2",
        env=env,
    )
    assert int(lookup.stdout) == redis_database


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
