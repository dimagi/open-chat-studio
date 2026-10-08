#!/usr/bin/env bash
# Set up dependencies and isolated services for any Git worktree provider.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/worktree-environment.sh
source "$SCRIPT_DIR/worktree-environment.sh"

ROOT_WORKTREE_PATH=$(ocs_root_worktree_path)
CURRENT_PATH=$(ocs_current_worktree_path)
cd "$CURRENT_PATH"

if ! ocs_is_root_worktree "$CURRENT_PATH" "$ROOT_WORKTREE_PATH"; then
    echo "[ocs] Setting up worktree at $CURRENT_PATH"

    for filename in .env .envrc .python-version; do
        if [[ -f "$ROOT_WORKTREE_PATH/$filename" && ! -f "$CURRENT_PATH/$filename" ]]; then
            cp "$ROOT_WORKTREE_PATH/$filename" "$CURRENT_PATH/$filename"
        fi
    done

    if command -v direnv >/dev/null 2>&1; then
        direnv allow "$CURRENT_PATH"
    fi
fi

python_version_file="$CURRENT_PATH/.python-version"
if [[ ! -f "$python_version_file" ]]; then
    python_version_file="$ROOT_WORKTREE_PATH/.python-version"
fi
if [[ -f "$python_version_file" ]]; then
    export UV_PYTHON
    UV_PYTHON=$(<"$python_version_file")
fi

# Prints the install's output once, and fails if the install did.
finish_node_install() {
    local status=0

    [[ -n "$node_install_pid" ]] || return 0
    wait "$node_install_pid" || status=$?
    node_install_pid=""
    cat "$node_install_log"
    return "$status"
}

# Hashing the migrations is the slowest bookkeeping step here, so it happens once and
# the result is handed to every step that needs it: the template name, the ancestor
# search and the dependency fingerprint.
migration_stamp_file=$(mktemp)
node_install_log=$(mktemp)
node_install_pid=""
trap 'finish_node_install || true; rm -f "$migration_stamp_file" "$node_install_log"' EXIT

# Nothing below needs the Node dependencies, so they install while the database is set up.
"$CURRENT_PATH/scripts/bootstrap.sh" --force --yes --skip-checks --only node \
    < /dev/null > "$node_install_log" 2>&1 &
node_install_pid=$!

"$CURRENT_PATH/scripts/bootstrap.sh" --force --yes --skip-checks --only python

ocs_migration_stamp_lines "$CURRENT_PATH" > "$migration_stamp_file"

if ocs_is_root_worktree "$CURRENT_PATH" "$ROOT_WORKTREE_PATH"; then
    finish_node_install
    ocs_record_dependency_fingerprint "$CURRENT_PATH" "$migration_stamp_file"
    echo "[ocs] Setup complete for root checkout."
    exit 0
fi

if [[ ! -f "$CURRENT_PATH/.env" ]]; then
    echo "[ocs] Cannot configure worktree services because the root checkout has no .env file." >&2
    exit 1
fi

resource_name=$(ocs_worktree_resource_name "$CURRENT_PATH")
database_url="postgres://postgres:postgres@localhost:5432/$resource_name"

ocs_set_env_value \
    "$CURRENT_PATH/.env" \
    OCS_WORKTREE_ID \
    "$resource_name"
ocs_set_env_value \
    "$CURRENT_PATH/.env" \
    DATABASE_URL \
    "$database_url"

export DATABASE_URL="$database_url"

template_name=$(ocs_template_name "$CURRENT_PATH" "$migration_stamp_file")
template_source=""

# Copying a template database is milliseconds of work where replaying every migration
# and seeding the result is minutes of it, so a fresh worktree only builds its database
# by hand when no template it can start from exists.
if ocs_database_exists "$resource_name"; then
    provisioning=migrate
    echo "[ocs] Migrating the existing $resource_name database."
elif ocs_templates_are_enabled && ocs_database_exists "$template_name"; then
    provisioning=copy
    template_source=$template_name
    echo "[ocs] Copying $template_name into $resource_name."
elif ocs_templates_are_enabled \
    && template_source=$(ocs_find_ancestor_template "$CURRENT_PATH" "$migration_stamp_file"); then
    provisioning=copy_and_migrate
    echo "[ocs] Copying $template_source into $resource_name and migrating forward."
else
    provisioning=build
    echo "[ocs] Building $resource_name from scratch; later worktrees copy the result."
fi

# The database is created before any Redis bookkeeping so that a PostgreSQL failure
# leaves no Redis allocation behind for a worktree that never finished setting up.
if [[ -n "$template_source" ]]; then
    # A template can go away between the check above and the copy: another worktree
    # prunes stale templates and drops and recreates the one it is snapshotting. That
    # costs this worktree its head start, not its database, so a copy that will not
    # succeed falls back to building by hand.
    if ! ocs_create_database "$resource_name" "$template_source"; then
        echo "[ocs] Could not copy $template_source; building $resource_name from scratch instead." >&2
        provisioning=build
        ocs_create_database "$resource_name"
    fi
elif [[ "$provisioning" == build ]]; then
    ocs_create_database "$resource_name"
fi

redis_database=$(ocs_allocate_redis_database "$resource_name")
redis_url="redis://localhost:6379/$redis_database"
ocs_set_env_value \
    "$CURRENT_PATH/.env" \
    REDIS_URL \
    "$redis_url"
export REDIS_URL="$redis_url"

# The stamp a template is named for covers this repository's migrations only, so an exact
# match still needs migrating unless the other inputs to `migrate` match as well. When
# they do not, the migrated copy replaces the template, so the next copy can skip it.
if [[ "$provisioning" == copy ]] && ocs_template_inputs_match "$CURRENT_PATH" "$template_source"; then
    echo "[ocs] $template_source is current; skipping migrate."
else
    if [[ "$provisioning" == copy ]]; then
        provisioning=copy_and_migrate
    fi
    uv run python manage.py migrate
fi

if [[ "$provisioning" == build ]] || ! ocs_database_is_seeded "$resource_name"; then
    # Seeding is not idempotent -- sample sessions, messages, traces and usage records
    # are created outright -- so copies inherit the seed data instead of re-running the
    # command over data that already exists. An existing database with no users at all
    # is the exception: a setup that died before finishing the seed left it that way.
    uv run python manage.py bootstrap_data \
        --email test@example.com \
        --password letmein \
        --superuser
fi

if [[ "$provisioning" == copy_and_migrate || "$provisioning" == build ]]; then
    ocs_snapshot_template \
        "$CURRENT_PATH" \
        "$template_name" \
        "$resource_name" \
        "$migration_stamp_file"
fi

ocs_prune_template_databases "$CURRENT_PATH" "$template_name"

finish_node_install
ocs_record_dependency_fingerprint "$CURRENT_PATH" "$migration_stamp_file"
echo "[ocs] Setup complete for $resource_name."
