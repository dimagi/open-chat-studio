# Maintaining Claude Code Agent Workflows

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [docs/developer_guides/claude_code_agent.md](../../docs/developer_guides/claude_code_agent.md).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Each run gives Claude access to the repository, a shell, and the GitHub CLI. Claude autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.

## Setup

The `ANTHROPIC_API_KEY` secret must be set under **Settings > Secrets and variables > Actions** in the repository. All Claude workflows require it.

## Workflow files

| File | Actions UI name | Trigger |
|---|---|---|
| `.github/workflows/claude.yml` | Claude Code | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch |
| `.github/workflows/claude-followup.yml` | Claude Followup | CI (i.e. Lint and Test) workflow completes on any `claude/**` branch |
| `.github/workflows/claude-dependabot.yml` | Claude Dependabot PR Review | Dependabot PR opened or updated, manual dispatch |
| `.github/workflows/claude-code-review.yml` | Claude Code Review | PR opened, marked ready for review, or pushed to (non-Dependabot, non-draft) |

## Forked PRs
Since fork PRs can't get an OIDC token, these pull requests do **not** run the Claude Code Review workflow.

## Tool allowlist

Each run is restricted to an explicit allowlist of tools defined in the `claude_args` field of the workflow file. Claude cannot call anything outside that list. If it needs a tool that isn't permitted, the run fails rather than silently taking an unintended action.

For more information on `claude_args`, see [GitHub for claude-code-action usage guide](https://github.com/anthropics/claude-code-action/blob/main/docs/usage.md).

## Plugins

The code-review workflow (`claude-code-review.yml`) uses a plugin from an external marketplace: `https://github.com/anthropics/claude-code.git`

## Concurrency

`claude.yml` uses `cancel-in-progress: false` — a second trigger for the same issue or PR waits for the in-progress run to finish rather than replacing it. Runs for different issues execute in parallel.

The code review workflow is the exception: a new push to a PR cancels any in-progress review of that PR (`cancel-in-progress: true`), since a review of stale code is wasted spend.

## Branch and label conventions

- **Branches** — all Claude-created branches are namespaced under `claude/` (e.g. `claude/123-20240518-143022` — issue number, date, time). Easy to target with branch protection rules.
- **`claude` label** — apply to an issue to trigger the one-shot or incremental workflow. Claude also applies it to PRs it opens.
- **`claude-followup-done` label** — applied by the follow-up workflow after it runs. Prevents a second round. Remove it manually if you need Claude to re-run follow-up on a PR.
