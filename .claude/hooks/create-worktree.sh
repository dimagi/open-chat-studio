#!/usr/bin/env bash
# Claude WorktreeCreate hook: create the worktree, then run the tool-neutral setup.
# Claude reads the worktree path from stdout, so everything else goes to stderr or the log.

set -euo pipefail

input=$(cat)
base_path=$(jq -r '.base_path // .cwd' <<<"$input")
source_ref=$(jq -r '.source_ref // empty' <<<"$input")
name=$(jq -r '.name // .worktree_name // empty' <<<"$input" \
    | sed -E 's#[^A-Za-z0-9._-]+#+#g; s#\.\.+#+#g; s#^[-.]+##')
if [[ -z "$name" ]]; then
    name="wt-$(date +%Y%m%d-%H%M%S)-$RANDOM"
fi

common_dir=$(git -C "$base_path" rev-parse --path-format=absolute --git-common-dir)
repo_root=$(dirname "$common_dir")
worktree_path="$repo_root/.claude/worktrees/$name"
branch="worktree-$name"

if [[ -z "$source_ref" ]]; then
    git -C "$repo_root" fetch --quiet origin >&2 || echo "[ocs] Fetch failed; using the last fetched ref." >&2
    source_ref=$(git -C "$repo_root" symbolic-ref --quiet --short refs/remotes/origin/HEAD || echo origin/main)
fi

mkdir -p "$(dirname "$worktree_path")"
# remove-worktree.sh keeps the branch, so reusing a name checks out the existing branch.
if git -C "$repo_root" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$repo_root" worktree add --quiet "$worktree_path" "$branch" >&2
else
    git -C "$repo_root" worktree add --quiet -b "$branch" "$worktree_path" "$source_ref" >&2
fi

log_file="${TMPDIR:-/tmp}/ocs-worktree-setup-$name.log"
if ! (cd "$worktree_path" && ./scripts/setup-worktree.sh) >"$log_file" 2>&1; then
    # The worktree is still usable; the SessionStart hook retries setup on the next launch.
    echo "Open Chat Studio setup failed. Full output: $log_file" >&2
    tail -n 40 "$log_file" >&2
fi

echo "$worktree_path"
