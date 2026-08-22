# Maintaining Claude Code Agent Workflows

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [Github automation with Claude](https://developers.openchatstudio.com/developer_guides/claude_github_automation/).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Claude autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.

## Setup secrets and permissions

- `ANTHROPIC_API_KEY`: Anthropic API key for Claude.
- `auto-update-models.yml`'s `reconcile` job needs `LLM_STATS_BEARER_TOKEN`.
- A workflow's `permissions:` block governs the default `GITHUB_TOKEN`.
- Each run is also restricted to an explicit allowlist of tools, passed via `--allowedTools` in the `claude_args` field of the workflow file — Claude cannot call anything outside that list. Together, `permissions:` and `--allowedTools` are the main safeguard against a compromised or malicious prompt (e.g. a hostile issue/PR body) taking unintended action.

> [!WARNING]
> If Claude tries to use a tool that isn't permitted, that call is denied and it continues without it — **the run won't fail**. A missing tool usually surfaces as an **incomplete result rather than an error**, so check the run transcript for denied tool calls if the output looks truncated.

## Workflow files

| File | Actions UI name | Trigger |
|---|---|---|
| `claude.yml` | Claude Code | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch |
| `claude-followup.yml` | Claude Followup | CI (i.e. Lint and Test) workflow completes on any `claude/**` branch |
| `claude-dependabot.yml` | Claude Dependabot PR Review | Dependabot PR opened or updated, manual dispatch |
| `claude-code-review.yml` | Claude Code Review | PR opened, marked ready for review, or pushed to (non-Dependabot, non-draft) |
| `auto-update-models.yml` | Auto Update LLM Models pricing | Daily schedule, manual dispatch |

## Forked PRs
Since fork PRs can't get an OIDC token, these pull requests do **not** run the Claude Code Review workflow.

## Plugins

The code-review workflow (`claude-code-review.yml`) uses a plugin from an external marketplace: `https://github.com/anthropics/claude-code.git`

## Concurrency

`claude.yml` uses `cancel-in-progress: false` — a second trigger for the same issue or PR waits for the in-progress run to finish rather than replacing it. Runs for different issues execute in parallel.

The code review workflow is the exception: a new push to a PR cancels any in-progress review of that PR (`cancel-in-progress: true`), since a review of stale code is wasted spend.

`auto-update-models.yml` uses a single global group (`auto-update-models`) instead of one per issue or PR, so only one run of the whole workflow — scheduled or manual — executes at a time across the repository.

## Branch and label conventions

- **Branches** — all Claude-created branches are namespaced under `claude/` (e.g. `claude/123-20240518-143022` — issue number, date, time). Easy to target with branch protection rules.
- **`claude` label** — apply to an issue to trigger the one-shot or incremental workflow. Claude also applies it to PRs it opens.
- **`claude-followup-done` label** — applied by the follow-up workflow after it runs. Prevents a second round. Remove it manually if you need Claude to re-run follow-up on a PR.
- **`auto-models` label** — applied by `auto-update-models.yml` to its new-model PR (alongside `claude`) and to its missing-pricing issue (alongside `cost-tracking`). The pricing-update PR from the same workflow gets no labels at all — it's opened by a plain `gh pr create` call in the deterministic `reconcile` job, not by Claude
