#!/usr/bin/env bash
# Quiet SessionStart guard shared by local coding agents.

set -euo pipefail

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
SCRIPT_DIR="$PROJECT_ROOT/scripts"
# shellcheck source=scripts/worktree-environment.sh
source "$SCRIPT_DIR/worktree-environment.sh"

if [[ -f "$PROJECT_ROOT/.env" \
    && -d "$PROJECT_ROOT/.venv" \
    && -d "$PROJECT_ROOT/node_modules" ]] \
    && ocs_dependencies_are_current "$PROJECT_ROOT"; then
    ROOT_WORKTREE_PATH=$(ocs_root_worktree_path)

    if ocs_is_root_worktree "$PROJECT_ROOT" "$ROOT_WORKTREE_PATH"; then
        exit 0
    fi

    resource_name=$(ocs_worktree_resource_name "$PROJECT_ROOT")
    if grep -Fqx \
        "DATABASE_URL=postgres://postgres:postgres@localhost:5432/$resource_name" \
        "$PROJECT_ROOT/.env"; then
        exit 0
    fi
fi

session_id=${CODEX_THREAD_ID:-${CLAUDE_SESSION_ID:-$$}}
log_file="${TMPDIR:-/tmp}/ocs-worktree-setup-${session_id}.log"
if ! "$PROJECT_ROOT/scripts/setup-worktree.sh" >"$log_file" 2>&1; then
    echo "Open Chat Studio setup failed. Full output: $log_file" >&2
    tail -n 40 "$log_file" >&2
    exit 1
fi
