#!/usr/bin/env bash
# Shared helpers for configuring isolated development resources per worktree.

set -euo pipefail

ocs_current_worktree_path() {
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

ocs_root_worktree_path() {
    git worktree list --porcelain | awk '/^worktree/{sub(/^worktree /, ""); print; exit}'
}

ocs_is_root_worktree() {
    [[ "$1" == "$2" ]]
}

ocs_sanitize_resource_name() {
    local sanitized
    sanitized=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_]+/_/g; s/^_+//; s/_+$//')
    sanitized=${sanitized:0:63}
    if [[ -z "$sanitized" ]]; then
        echo "Unable to derive a safe worktree resource name." >&2
        return 1
    fi
    printf '%s\n' "$sanitized"
}

ocs_worktree_resource_name() {
    local current_path="$1"
    local branch_name parent_name raw_name

    if [[ -n "${OCS_WORKTREE_ID:-}" ]]; then
        raw_name=$OCS_WORKTREE_ID
    elif [[ "$current_path" == */.codex/worktrees/*/* ]]; then
        parent_name=$(basename "$(dirname "$current_path")")
        raw_name="codex_${parent_name}"
    else
        branch_name=$(git -C "$current_path" branch --show-current)
        if [[ -n "$branch_name" ]]; then
            raw_name=$branch_name
        else
            raw_name="worktree_$(printf '%s' "$current_path" | cksum | awk '{print $1}')"
        fi
    fi

    ocs_sanitize_resource_name "$raw_name"
}

ocs_redis_database() {
    printf '%s' "$1" | cksum | awk '{print ($1 % 15) + 1}'
}

ocs_dependency_fingerprint() {
    local worktree_path="$1"
    local dependency_file
    local dependency_files=(
        .python-version
        .npmrc
        package.json
        pnpm-lock.yaml
        pyproject.toml
        uv.lock
        scripts/bootstrap.sh
    )

    for dependency_file in "${dependency_files[@]}"; do
        printf '%s:' "$dependency_file"
        if [[ -f "$worktree_path/$dependency_file" ]]; then
            cksum < "$worktree_path/$dependency_file"
        else
            printf 'missing\n'
        fi
    done | cksum | awk '{print $1 ":" $2}'
}

ocs_record_dependency_fingerprint() {
    local worktree_path="$1"
    local fingerprint_file="$worktree_path/.venv/.ocs-dependency-fingerprint"

    mkdir -p "$(dirname "$fingerprint_file")"
    ocs_dependency_fingerprint "$worktree_path" > "$fingerprint_file"
}

ocs_dependencies_are_current() {
    local worktree_path="$1"
    local fingerprint_file="$worktree_path/.venv/.ocs-dependency-fingerprint"
    local expected_fingerprint

    [[ -f "$fingerprint_file" ]] || return 1
    expected_fingerprint=$(ocs_dependency_fingerprint "$worktree_path")
    [[ "$(<"$fingerprint_file")" == "$expected_fingerprint" ]]
}

ocs_set_env_value() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    local temp_file

    temp_file=$(mktemp "${env_file}.XXXXXX")
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 {
            print key "=" value
            found = 1
            next
        }
        { print }
        END {
            if (!found) {
                print key "=" value
            }
        }
    ' "$env_file" > "$temp_file"
    mv "$temp_file" "$env_file"
}
