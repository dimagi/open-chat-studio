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
    if [ -f "$ROOT_WORKTREE_PATH/.python-version" ] && [ ! -f ".python-version" ]; then
        cp "$ROOT_WORKTREE_PATH/.python-version" .python-version
    fi
    command -v direnv &>/dev/null        && direnv allow
fi

# Set UV_PYTHON from .python-version so uv sync uses the pinned version rather
# than the latest Python satisfying requires-python in pyproject.toml.
_PV_FILE="${CURRENT_PATH}/.python-version"
if [ ! -f "$_PV_FILE" ]; then
    _PV_FILE="$ROOT_WORKTREE_PATH/.python-version"
fi
if [ -f "$_PV_FILE" ]; then
    export UV_PYTHON
    UV_PYTHON=$(cat "$_PV_FILE")
fi

"$ROOT_WORKTREE_PATH/scripts/bootstrap.sh" --force --yes

echo "[ocs] Setup complete."
