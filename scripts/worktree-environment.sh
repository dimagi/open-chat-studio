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
    local original="$1"
    local hash_suffix
    local prefix_length
    local sanitized
    sanitized=$(printf '%s' "$original" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_]+/_/g; s/^_+//; s/_+$//')
    if [[ -z "$sanitized" ]]; then
        echo "Unable to derive a safe worktree resource name." >&2
        return 1
    fi

    if [[ "$sanitized" != "$original" || ${#sanitized} -gt 63 ]]; then
        hash_suffix=$(printf '%s' "$original" | cksum | awk '{printf "%08x", $1}')
        prefix_length=$((63 - ${#hash_suffix} - 1))
        sanitized="${sanitized:0:prefix_length}_${hash_suffix}"
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

ocs_redis_registry_command() {
    local operation="$1"
    local resource_name="$2"
    local expected_database="${3:-}"
    local registry_database="${OCS_REDIS_REGISTRY_DATABASE:-15}"
    local registry_key="ocs:worktree:redis-registry"
    local registry_script

    registry_script='local operation = ARGV[1]
local resource = ARGV[2]
local expected_database = ARGV[3]
local resource_field = "resource:" .. resource

local function database_field(database)
    return "database:" .. database
end

if operation == "lookup" then
    local database = redis.call("HGET", KEYS[1], resource_field)
    if not database then
        return "missing"
    end
    if redis.call("HGET", KEYS[1], database_field(database)) ~= resource then
        return "missing"
    end
    return database
end


if operation == "allocate" then
    local database = redis.call("HGET", KEYS[1], resource_field)
    if database and redis.call("HGET", KEYS[1], database_field(database)) == resource then
        return database
    end
    redis.call("HDEL", KEYS[1], resource_field)

    for candidate = 1, 14 do
        local field = database_field(candidate)
        if not redis.call("HGET", KEYS[1], field) then
            redis.call("HSET", KEYS[1], field, resource)
            redis.call("HSET", KEYS[1], resource_field, candidate)
            return candidate
        end
    end
    return redis.error_reply("No Redis databases available for another worktree")
end


if operation == "release" then
    local database = redis.call("HGET", KEYS[1], resource_field)
    if database ~= expected_database then
        return 0
    end
    if redis.call("HGET", KEYS[1], database_field(database)) ~= resource then
        return 0
    end
    redis.call("HDEL", KEYS[1], resource_field, database_field(database))
    return 1
end


return redis.error_reply("Unknown worktree Redis registry operation")'

    redis-cli \
        -e \
        -n "$registry_database" \
        --raw \
        EVAL "$registry_script" 1 "$registry_key" \
        "$operation" "$resource_name" "$expected_database"
}

ocs_allocate_redis_database() {
    local resource_name="$1"
    local database

    database=$(ocs_redis_registry_command allocate "$resource_name")
    if [[ ! "$database" =~ ^([1-9]|1[0-4])$ ]]; then
        echo "Unable to allocate a Redis database for $resource_name: ${database:-no response}" >&2
        return 1
    fi
    printf '%s\n' "$database"
}

ocs_lookup_redis_database() {
    local resource_name="$1"
    local database

    database=$(ocs_redis_registry_command lookup "$resource_name") || return 2
    if [[ "$database" == "missing" ]]; then
        return 1
    fi
    if [[ ! "$database" =~ ^([1-9]|1[0-4])$ ]]; then
        echo "Invalid Redis database allocation for $resource_name: ${database:-no response}" >&2
        return 2
    fi
    printf '%s\n' "$database"
}

ocs_release_redis_database() {
    local resource_name="$1"
    local database="$2"
    local released

    released=$(ocs_redis_registry_command release "$resource_name" "$database")
    if [[ "$released" != "1" ]]; then
        echo "Unable to release Redis database $database for $resource_name." >&2
        return 1
    fi
}

ocs_dependency_fingerprint() {
    local worktree_path="$1"
    local dependency_file
    local migration_file
    local relative_migration_file
    local dependency_files=(
        .python-version
        .npmrc
        package.json
        pnpm-lock.yaml
        pyproject.toml
        uv.lock
        scripts/bootstrap.sh
    )

    {
        for dependency_file in "${dependency_files[@]}"; do
            printf '%s:' "$dependency_file"
            if [[ -f "$worktree_path/$dependency_file" ]]; then
                cksum < "$worktree_path/$dependency_file"
            else
                printf 'missing\n'
            fi
        done

        if [[ -d "$worktree_path/apps" ]]; then
            while IFS= read -r -d '' migration_file; do
                relative_migration_file=${migration_file#"$worktree_path/"}
                printf '%s:' "$relative_migration_file"
                cksum < "$migration_file"
            done < <(
                find "$worktree_path/apps" \
                    -type f \
                    -path '*/migrations/*.py' \
                    -print0 \
                    | sort -z
            )
        fi
    } | cksum | awk '{print $1 ":" $2}'
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
