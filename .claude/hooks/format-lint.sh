#!/bin/bash
# Auto-format and lint files after Claude edits them.
# Triggered by PostToolUse on Write, Edit, and NotebookEdit.

set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

if [ -z "$file_path" ]; then
  exit 0
fi

# Resolve to absolute path
if [[ "$file_path" != /* ]]; then
  file_path="$CLAUDE_PROJECT_DIR/$file_path"
fi

if [ ! -f "$file_path" ]; then
  exit 0
fi

ext="${file_path##*.}"

case "$ext" in
  py)
    ruff check --fix "$file_path" 2>&1 || true
    ruff format "$file_path" 2>&1 || true
    ;;
  js|ts|jsx|tsx)
    cd "$CLAUDE_PROJECT_DIR"
    npm run lint "$file_path" 2>&1 || true
    ;;
esac

exit 0
