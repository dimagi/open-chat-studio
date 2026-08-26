#!/usr/bin/env bash
# Claude SessionStart wrapper around the tool-neutral worktree setup.

set -euo pipefail

PROJECT_ROOT=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
"$PROJECT_ROOT/scripts/ensure-worktree-setup.sh"

# Ask the user if they want to run Claude Code.
# Only prompt when stdin/stdout are a TTY (interactive terminal) and guard
# against recursive re-entry when `claude` is launched from this hook.
if [[ -t 0 && -t 1 && -z "${OCS_CLAUDE_LAUNCH_HANDLED:-}" ]]; then
    export OCS_CLAUDE_LAUNCH_HANDLED=1
    read -r -p "[ocs] Do you want to run Claude Code? (y/N): " RUN_CLAUDE || true
    if [[ "$RUN_CLAUDE" =~ ^[Yy]$ ]]; then
        read -rep "[ocs] Enter your prompt: " CLAUDE_PROMPT || true
        read -rep "[ocs] Run with all permissions (--dangerously-skip-permissions)? (y/N): " SKIP_PERMS || true
        if [[ "$SKIP_PERMS" =~ ^[Yy]$ ]]; then
            claude --dangerously-skip-permissions "$CLAUDE_PROMPT"
        else
            claude "$CLAUDE_PROMPT"
        fi
    fi
fi
