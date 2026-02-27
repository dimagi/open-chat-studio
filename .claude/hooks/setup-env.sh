#!/usr/bin/env bash
# Run on SessionStart to set up the development environment.
# In a worktree: copies env files from the main worktree first.
# Always: installs dependencies via bootstrap.sh.

set -euo pipefail

ROOT_WORKTREE_PATH=$(git worktree list --porcelain | awk '/^worktree/{sub(/^worktree /, ""); print; exit}')
CURRENT_PATH=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

if [ "$CURRENT_PATH" != "$ROOT_WORKTREE_PATH" ]; then
    echo "[ocs] Setting up worktree at $CURRENT_PATH"
    export ROOT_WORKTREE_PATH

    [ -f "$ROOT_WORKTREE_PATH/.env" ]   && [ ! -f ".env" ]   && cp "$ROOT_WORKTREE_PATH/.env"   .env
    [ -f "$ROOT_WORKTREE_PATH/.envrc" ] && [ ! -f ".envrc" ] && cp "$ROOT_WORKTREE_PATH/.envrc" .envrc
    command -v direnv &>/dev/null        && direnv allow
fi

"$ROOT_WORKTREE_PATH/scripts/bootstrap.sh" --force --yes

echo "[ocs] Setup complete."
