# Maintaining Claude Code Agent Workflows

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [docs/developer_guides/igor.md](../../docs/developer_guides/igor.md).

## Setup

The `ANTHROPIC_API_KEY` secret must be set under **Settings > Secrets and variables > Actions** in the repository. All three Claude workflows require it.

## Workflow files

| File | Actions UI name | Trigger |
|---|---|---|
| `.github/workflows/claude.yml` | Claude Code | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch |
| `.github/workflows/claude-followup.yml` | Claude Followup | CI completes on any `claude/**` branch |
| `.github/workflows/claude-dependabot.yml` | Claude Dependabot PR Review | Dependabot PR opened or updated, manual dispatch |

## Tool allowlist

Each run is restricted to an explicit allowlist of tools defined in the `claude_args` field of the workflow file. Claude cannot call anything outside that list. If it needs a tool that isn't permitted, the run fails rather than silently taking an unintended action.

For more information on `claude_args` see [GitHub for claude-code-action usage guide](https://github.com/anthropics/claude-code-action/blob/main/docs/usage.md).

## Concurrency

Runs on the same issue queue instead of cancelling each other (`cancel-in-progress: false`). If a second trigger fires while a run is in progress for the same issue, it waits. Runs for different issues execute in parallel.

## Branch and label conventions

- **Branches** — all Claude-created branches are namespaced under `claude/` (e.g. `claude/123-20240518-143022` — issue number, date, time). Easy to target with branch protection rules.
- **`claude` label** — apply to an issue to trigger the one-shot or incremental workflow. Claude also applies it to PRs it opens.
- **`claude-followup-done` label** — applied by the follow-up workflow after it runs. Prevents a second round. Remove it manually if you need Claude to re-run follow-up on a PR.
