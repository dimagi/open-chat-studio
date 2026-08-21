#!/usr/bin/env bash
# Remove the isolated PostgreSQL and Redis resources for a worktree.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/worktree-environment.sh
source "$SCRIPT_DIR/worktree-environment.sh"

ROOT_WORKTREE_PATH=$(ocs_root_worktree_path)
CURRENT_PATH=$(ocs_current_worktree_path)

if ocs_is_root_worktree "$CURRENT_PATH" "$ROOT_WORKTREE_PATH"; then
    if [[ "${OCS_ALLOW_ROOT_TEARDOWN:-false}" != "true" ]]; then
        echo "[ocs] Refusing to clean resources from the root checkout."
        exit 0
    fi
    if [[ -z "${OCS_WORKTREE_ID:-}" ]]; then
        echo "[ocs] OCS_WORKTREE_ID is required when cleaning from the root checkout." >&2
        exit 1
    fi
fi

resource_name=$(ocs_worktree_resource_name "$CURRENT_PATH")

PGPASSWORD=postgres psql \
    -h localhost \
    -U postgres \
    -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$resource_name\" WITH (FORCE)"

if redis_database=$(ocs_lookup_redis_database "$resource_name"); then
    redis-cli -n "$redis_database" FLUSHDB
    ocs_release_redis_database "$resource_name" "$redis_database"
else
    lookup_status=$?
    if [[ "$lookup_status" -ne 1 ]]; then
        exit "$lookup_status"
    fi
fi

echo "[ocs] Cleaned resources for $resource_name."
