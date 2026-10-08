#!/usr/bin/env bash
# Claude WorktreeRemove hook: drop the worktree's database and Redis allocation, then remove it.

set -euo pipefail

input=$(cat)
base_path=$(jq -r '.base_path // .cwd' <<<"$input")
worktree_path=$(jq -r '.worktree_path // empty' <<<"$input")
if [[ -z "$worktree_path" ]]; then
    echo "[ocs] No worktree_path in the WorktreeRemove input." >&2
    exit 1
fi

if [[ -x "$worktree_path/scripts/teardown-worktree.sh" ]]; then
    (cd "$worktree_path" && ./scripts/teardown-worktree.sh) >&2 \
        || echo "[ocs] Teardown failed for $worktree_path; removing the worktree anyway." >&2
fi

git -C "$base_path" worktree remove --force "$worktree_path"

if [[ -d "$worktree_path" ]]; then
    echo "Failed to remove worktree at $worktree_path" >&2
    exit 1
fi
