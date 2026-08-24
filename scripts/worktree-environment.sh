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

ocs_persisted_worktree_resource_name() {
    local current_path="$1"
    local env_file="$current_path/.env"
    local persisted_name

    [[ -f "$env_file" ]] || return 1
    persisted_name=$(awk '
        index($0, "OCS_WORKTREE_ID=") == 1 {
            value = substr($0, length("OCS_WORKTREE_ID=") + 1)
        }
        END {
            if (value != "") {
                print value
            }
        }
    ' "$env_file")
    [[ -n "$persisted_name" ]] || return 1
    if [[ ! "$persisted_name" =~ ^[a-z0-9_]{1,63}$ ]]; then
        echo "Invalid persisted worktree resource name: $persisted_name" >&2
        return 2
    fi
    printf '%s\n' "$persisted_name"
}

ocs_worktree_resource_name() {
    local current_path="$1"
    local branch_name parent_name persisted_status raw_name

    if [[ -n "${OCS_WORKTREE_ID:-}" ]]; then
        raw_name=$OCS_WORKTREE_ID
    else
        if raw_name=$(ocs_persisted_worktree_resource_name "$current_path"); then
            :
        else
            persisted_status=$?
            if [[ "$persisted_status" -ne 1 ]]; then
                return "$persisted_status"
            fi
            if [[ "$current_path" == */.codex/worktrees/*/* ]]; then
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

# One line per migration file, carrying its path and checksum. The lines are both
# the migration part of the dependency fingerprint and the stamp recorded against a
# template database, which is why they name the file instead of only checksumming it:
# comparing two stamps then tells us whether one migration set is an ancestor of the
# other, not merely that they differ.
ocs_migration_stamp_lines() {
    local worktree_path="$1"

    [[ -d "$worktree_path/apps" ]] || return 0

    # Checksumming each migration in its own process costs a fork per file, and there
    # are hundreds of them; batching the whole set into a handful of `cksum` calls is
    # an order of magnitude quicker. `cksum` prints "<checksum> <bytes> <path>", which
    # awk turns back into the "<path>:<checksum> <bytes>" line this function reports.
    (
        cd "$worktree_path" || exit 0
        find apps \
            -type f \
            -path '*/migrations/*.py' \
            -exec cksum {} + \
            | awk '{ printf "%s:%s %s\n", $3, $1, $2 }' \
            | LC_ALL=C sort
    )
}

# The stamp lines for a worktree, preferring a copy an earlier step already computed.
# Hashing every migration is expensive enough to be worth doing once per run and
# handing the result to everything else that needs it.
ocs_read_migration_stamp() {
    local worktree_path="$1"
    local stamp_lines_file="${2:-}"

    if [[ -n "$stamp_lines_file" && -f "$stamp_lines_file" ]]; then
        cat "$stamp_lines_file"
    else
        ocs_migration_stamp_lines "$worktree_path"
    fi
}

ocs_dependency_fingerprint() {
    local worktree_path="$1"
    local stamp_lines_file="${2:-}"
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

    {
        for dependency_file in "${dependency_files[@]}"; do
            printf '%s:' "$dependency_file"
            if [[ -f "$worktree_path/$dependency_file" ]]; then
                cksum < "$worktree_path/$dependency_file"
            else
                printf 'missing\n'
            fi
        done

        ocs_read_migration_stamp "$worktree_path" "$stamp_lines_file"
    } | cksum | awk '{print $1 ":" $2}'
}

ocs_record_dependency_fingerprint() {
    local worktree_path="$1"
    local stamp_lines_file="${2:-}"
    local fingerprint_file="$worktree_path/.venv/.ocs-dependency-fingerprint"

    mkdir -p "$(dirname "$fingerprint_file")"
    ocs_dependency_fingerprint "$worktree_path" "$stamp_lines_file" > "$fingerprint_file"
}

ocs_dependencies_are_current() {
    local worktree_path="$1"
    local fingerprint_file="$worktree_path/.venv/.ocs-dependency-fingerprint"
    local expected_fingerprint

    [[ -f "$fingerprint_file" ]] || return 1
    expected_fingerprint=$(ocs_dependency_fingerprint "$worktree_path")
    [[ "$(<"$fingerprint_file")" == "$expected_fingerprint" ]]
}

# Provisioning a worktree database means replaying every migration and then seeding it,
# which takes minutes and produces the same result every time. Instead we keep that
# result once per migration set as a template database and let Postgres copy it, which
# takes milliseconds. Templates are content-addressed by the migration stamp, so a
# branch that adds migrations gets its own template rather than mutating a shared one.
OCS_TEMPLATE_NAME_PREFIX="ocs_tmpl_"
OCS_TEMPLATE_RETENTION="${OCS_TEMPLATE_RETENTION:-3}"
OCS_TEMPLATE_COPY_RETRY_DELAY="${OCS_TEMPLATE_COPY_RETRY_DELAY:-2}"

ocs_templates_are_enabled() {
    [[ "${OCS_DISABLE_DATABASE_TEMPLATES:-false}" != "true" ]]
}

ocs_psql() {
    local database="$1"
    shift

    PGPASSWORD=postgres psql \
        -h localhost \
        -U postgres \
        -d "$database" \
        -v ON_ERROR_STOP=1 \
        "$@"
}

ocs_database_exists() {
    ocs_psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$1'" | grep -q 1
}

ocs_create_database() {
    local database_name="$1"
    local template_name="${2:-}"
    local attempt
    local statement

    if [[ -z "$template_name" ]]; then
        ocs_psql postgres -c "CREATE DATABASE \"$database_name\""
        return
    fi

    # Postgres refuses to copy a database while anything else is connected to it, so a
    # second worktree setting itself up at the same moment can lose the race.
    statement="CREATE DATABASE \"$database_name\" TEMPLATE \"$template_name\""
    for attempt in 1 2 3; do
        if ocs_psql postgres -c "$statement"; then
            return 0
        fi
        if [[ "$attempt" -lt 3 ]]; then
            echo "[ocs] Retrying the copy of $template_name into $database_name." >&2
            sleep "$OCS_TEMPLATE_COPY_RETRY_DELAY"
        fi
    done
    return 1
}

ocs_drop_database() {
    ocs_psql postgres -c "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)"
}

# Whether a database already carries the sample data `bootstrap_data` creates. A
# setup that died between creating the database and finishing the seed would
# otherwise leave that database empty forever, because every provisioning mode but
# `build` skips seeding. A query that fails counts as seeded: re-running a seed that
# is not idempotent over data that already exists is the worse outcome.
ocs_database_is_seeded() {
    local user_count

    user_count=$(ocs_psql "$1" -tAc "SELECT count(*) FROM users_customuser" 2>/dev/null) || return 0
    [[ "$user_count" != "0" ]]
}

ocs_migration_stamp_id() {
    ocs_read_migration_stamp "$1" "${2:-}" | cksum | awk '{printf "%08x%08x\n", $1, $2}'
}

# Templates from one clone are useless to another -- the stamp that says what a
# template holds lives in the clone that built it -- so each clone gets its own
# namespace. Sharing the prefix instead would have a second clone of the same
# repository prune every template it cannot find a stamp for, which is all of them.
ocs_template_prefix() {
    local worktree_path="$1"

    printf '%s%s_\n' \
        "$OCS_TEMPLATE_NAME_PREFIX" \
        "$(ocs_template_stamp_directory "$worktree_path" | cksum | awk '{printf "%08x", $1}')"
}

ocs_template_name() {
    printf '%s%s\n' "$(ocs_template_prefix "$1")" "$(ocs_migration_stamp_id "$1" "${2:-}")"
}

# Stamps live beside the shared git directory rather than inside the template itself so
# that reading one costs no database connection: opening a template is exactly what
# makes a concurrent copy of it fail.
ocs_template_stamp_directory() {
    local worktree_path="$1"
    local git_common_dir

    git_common_dir=$(git -C "$worktree_path" rev-parse --git-common-dir)
    if [[ "$git_common_dir" != /* ]]; then
        git_common_dir=$(cd "$worktree_path" && cd "$git_common_dir" && pwd)
    fi
    printf '%s/ocs-db-templates\n' "$git_common_dir"
}

ocs_template_stamp_path() {
    printf '%s/%s.stamp\n' "$(ocs_template_stamp_directory "$1")" "$2"
}

ocs_list_template_databases() {
    local prefix
    prefix=$(ocs_template_prefix "$1")

    # Newest first: object ids rise with creation order, which is the order we prune in.
    ocs_psql postgres -tAc "
        SELECT datname
        FROM pg_database
        WHERE left(datname, ${#prefix}) = '$prefix'
        ORDER BY oid DESC
    "
}

# The closest template whose migrations are all still present, unchanged, in this
# worktree. Such a template can be copied and then migrated forward. One holding a
# migration this branch does not have would leave schema behind that nothing can
# unapply, so it is skipped.
ocs_find_ancestor_template() {
    local worktree_path="$1"
    local stamp_lines_file="$2"
    local best_template=""
    local best_size=-1
    local candidate_stamp
    local missing_migrations
    local size
    local sorted_candidate
    local sorted_current
    local template

    missing_migrations=$(mktemp)
    sorted_candidate=$(mktemp)
    sorted_current=$(mktemp)
    LC_ALL=C sort "$stamp_lines_file" > "$sorted_current"

    while IFS= read -r template; do
        [[ -n "$template" ]] || continue
        candidate_stamp=$(ocs_template_stamp_path "$worktree_path" "$template")
        [[ -f "$candidate_stamp" ]] || continue

        LC_ALL=C sort "$candidate_stamp" > "$sorted_candidate"
        LC_ALL=C comm -23 "$sorted_candidate" "$sorted_current" > "$missing_migrations"
        [[ -s "$missing_migrations" ]] && continue

        size=$(wc -l < "$sorted_candidate")
        if [[ "$size" -gt "$best_size" ]]; then
            best_size=$size
            best_template=$template
        fi
    done < <(ocs_list_template_databases "$worktree_path")

    rm -f "$missing_migrations" "$sorted_candidate" "$sorted_current"
    [[ -n "$best_template" ]] || return 1
    printf '%s\n' "$best_template"
}

# Snapshot a freshly provisioned worktree database as the template for its migration
# set. Failing to do so only costs the next worktree its head start, so it is a
# warning rather than a setup failure.
ocs_snapshot_template() {
    local worktree_path="$1"
    local template_name="$2"
    local source_database="$3"
    local stamp_lines_file="$4"
    local stamp_path

    ocs_templates_are_enabled || return 0

    if ! ocs_drop_database "$template_name" \
        || ! ocs_create_database "$template_name" "$source_database"; then
        echo "[ocs] Could not snapshot $template_name; the next worktree will migrate from scratch." >&2
        return 0
    fi

    # A template nothing can read the stamp of is only usable for an exact-name copy
    # and gets pruned on the next run, so a failed stamp is worth saying out loud --
    # but not worth failing a setup whose database is already finished.
    stamp_path=$(ocs_template_stamp_path "$worktree_path" "$template_name")
    if ! { mkdir -p "$(dirname "$stamp_path")" && cp "$stamp_lines_file" "$stamp_path"; }; then
        echo "[ocs] Could not record the stamp for $template_name; it will be pruned rather than reused." >&2
    fi
}

# Keep the newest few templates plus whichever one this setup relies on, and drop the
# rest along with any template whose stamp has gone missing.
ocs_prune_template_databases() {
    local worktree_path="$1"
    local retained_template="${2:-}"
    local kept=0
    local stamp_directory
    local stamp_path
    local template

    ocs_templates_are_enabled || return 0
    stamp_directory=$(ocs_template_stamp_directory "$worktree_path")

    while IFS= read -r template; do
        [[ -n "$template" ]] || continue
        stamp_path="$stamp_directory/$template.stamp"
        if [[ "$template" == "$retained_template" ]]; then
            kept=$((kept + 1))
            continue
        fi
        if [[ -f "$stamp_path" && "$kept" -lt "$OCS_TEMPLATE_RETENTION" ]]; then
            kept=$((kept + 1))
            continue
        fi
        echo "[ocs] Dropping the stale template database $template."
        ocs_drop_database "$template"
        rm -f "$stamp_path"
    done < <(ocs_list_template_databases "$worktree_path")
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
