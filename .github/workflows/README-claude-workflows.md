# GitHub Automation with Claude

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [Github automation with Claude](https://developers.openchatstudio.com/developer_guides/claude_github_automation/).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Claude autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.

## Required secrets and permissions

- `ANTHROPIC_API_KEY` for Claude Code.
- `auto-update-models.yml`'s `reconcile` job needs `LLM_STATS_BEARER_TOKEN`.
- Each workflow's `permissions:` block governs the default `GITHUB_TOKEN`.
- Each run is also restricted to an explicit allowlist of tools, passed via `--allowedTools` in the `claude_args` field of the workflow file — Claude cannot call anything outside that list. Together, `permissions:` and `--allowedTools` are the main safeguard against a compromised or malicious prompt (e.g. a hostile issue/PR body) taking unintended action.

> [!WARNING]
> If Claude tries to use a tool that isn't permitted, that call is denied and it continues without it — **the run won't fail**. A missing tool usually surfaces as an **incomplete result rather than an error**, so check the run transcript for denied tool calls if the output looks truncated.

## GitHub workflows at a glance

| File | Actions UI name | Trigger |
|---|---|---|
| `claude.yml` | Claude Code | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch |
| `claude-followup.yml` | Claude Followup | CI (i.e. Lint and Test) workflow completes on any `claude/**` branch |
| `claude-dependabot.yml` | Claude Dependabot PR Review | Dependabot PR opened or updated, manual dispatch |
| `claude-code-review.yml` | Claude Code Review | PR opened, marked ready for review, or pushed (non-Dependabot, non-draft) |
| `auto-update-models.yml` | Auto Update LLM Models pricing | Daily schedule, manual dispatch |

## Forked PR limitations
Since fork PRs can't get an OIDC token, these pull requests do **not** run the Claude Code Review workflow.

## Claude Code plugins

The code-review workflow (`claude-code-review.yml`) uses an official Anthropic plugin.

## Concurrency and run cancellation

- `claude.yml` and `claude-followup.yml` both use `cancel-in-progress: false`, grouped per issue/PR and per branch respectively — a second trigger for the same issue, PR, or branch waits for the in-progress run to finish rather than replacing it. Runs for different issues, PRs, or branches execute in parallel.
- `auto-update-models.yml` uses a single global group (`auto-update-models`) instead of one per issue or PR, so only one run of the whole workflow — scheduled or manual — executes at a time across the repository.
- `claude-code-review.yml` is the exception that cancels: a new push to a PR cancels any in-progress review of that PR (`cancel-in-progress: true`), since a review of stale code is wasted spend.
- `claude-dependabot.yml` sets no concurrency group at all, so its runs are independent of one another.

## GitHub labels used by these workflows

- **`claude` label** — apply to an issue to trigger the one-shot or incremental workflow. Claude also applies it to PRs it opens, including the new-model PR from `auto-update-models.yml`.
- **`claude-followup-done` label** — applied by the follow-up workflow after it runs. Prevents a second round. Remove it manually if you need Claude to re-run follow-up on a PR.
- **`auto-models` label** — applied by `auto-update-models.yml` to both its new-model PR and to its missing-pricing issue.
- **`cost-tracking` label** — applied by `auto-update-models.yml` to its missing-pricing issue.

## Troubleshooting
- **Run fails immediately in the `claude-code-action` step** — check that `ANTHROPIC_API_KEY` is set and valid; that's the most common cause across all these workflows.
- **`claude-code-review.yml` didn't run on a PR from a fork** — expected, see Forked PRs above. There's no failed job to debug; the run was skipped.
- **`auto-update-models.yml`'s `reconcile` job fails** — verify `LLM_STATS_BEARER_TOKEN` is set and valid; the job needs it to call the llm-stats.com Stats API.
- **A Claude-created PR didn't get a second follow-up round** — by design, see the `claude-followup-done` label above. Remove the label, or comment `@claude` on the PR, to trigger another pass.
- **Output looks incomplete, or a step Claude should have taken didn't happen** — check the run transcript for denied tool calls, see Tool allowlist above.
- **Output quality needs improvement** — comment `@claude` on the issue or PR with what to revise, or update the relevant prompt in the workflow file if the issue is systemic.
