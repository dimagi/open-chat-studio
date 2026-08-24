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

"$CURRENT_PATH/scripts/bootstrap.sh" --force --yes

if ocs_is_root_worktree "$CURRENT_PATH" "$ROOT_WORKTREE_PATH"; then
    ocs_record_dependency_fingerprint "$CURRENT_PATH"
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

if ! PGPASSWORD=postgres psql \
    -h localhost \
    -U postgres \
    -tAc "SELECT 1 FROM pg_database WHERE datname='$resource_name'" \
    | grep -q 1; then
    PGPASSWORD=postgres psql \
        -h localhost \
        -U postgres \
        -v ON_ERROR_STOP=1 \
        -c "CREATE DATABASE \"$resource_name\""
fi

redis_database=$(ocs_allocate_redis_database "$resource_name")
redis_url="redis://localhost:6379/$redis_database"
ocs_set_env_value \
    "$CURRENT_PATH/.env" \
    REDIS_URL \
    "$redis_url"
export REDIS_URL="$redis_url"

uv run python manage.py migrate
uv run python manage.py bootstrap_data \
    --email test@example.com \
    --password letmein \
    --superuser

ocs_record_dependency_fingerprint "$CURRENT_PATH"
echo "[ocs] Setup complete for $resource_name."
