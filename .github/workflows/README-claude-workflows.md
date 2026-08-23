# GitHub Automation with Claude

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [Github automation with Claude](https://developers.openchatstudio.com/developer_guides/claude_github_automation/).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Claude autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.

## Required secrets and permissions

- Secret requirements differ per workflow — check the `Requirements:` line in each workflow file's header comment.
- Each workflow's `permissions:` block governs the default `GITHUB_TOKEN`.
- Each run is also restricted to an explicit allowlist of tools, passed via `--allowedTools` in the `claude_args` field of the workflow file — Claude cannot call anything outside that list. Together, `permissions:` and `--allowedTools` are the main safeguard against a compromised or malicious prompt (e.g. a hostile issue/PR body) taking unintended action.

> [!WARNING]
> If Claude tries to use a tool that isn't permitted, that call is denied and it continues without it — **the run won't fail**. A missing tool usually surfaces as an **incomplete result rather than an error**, so check the run transcript for denied tool calls if the output looks truncated.

## GitHub workflows at a glance

| File | Actions UI name |
|---|---|
| `claude.yml` | Claude Code |
| `claude-followup.yml` | Claude Followup |
| `claude-dependabot.yml` | Claude Dependabot PR Review |
| `claude-code-review.yml` | Claude Code Review |
| `auto-update-models.yml` | Auto Update LLM Models |

Check the comment block at the top of each file for what it does, when it triggers, and what it needs.

## Forked PR limitations

`claude-code-review.yml` skips fork PRs — see the `Notes:` in its header comment for why.

## Concurrency and run cancellation

- `claude.yml` and `claude-followup.yml` both use `cancel-in-progress: false`, grouped per issue/PR and per branch respectively — a second trigger for the same issue, PR, or branch waits for the in-progress run to finish rather than replacing it. Runs for different issues, PRs, or branches execute in parallel.
- `auto-update-models.yml` uses a single global group (`auto-update-models`) instead of one per issue or PR, so only one run of the whole workflow — scheduled or manual — executes at a time across the repository.
- `claude-code-review.yml` is the exception that cancels: a new push to a PR cancels any in-progress review of that PR (`cancel-in-progress: true`), since a review of stale code is wasted spend.
- `claude-dependabot.yml` sets no concurrency group at all, so its runs are independent of one another.

## GitHub labels used by these workflows

- **`claude` label** — apply to an issue to trigger the one-shot or incremental workflow. Claude also applies it to PRs it opens, including the new-model PR from `auto-update-models.yml`.
- **`claude-followup-done` label** — one-round limiter for the follow-up workflow; see the header comment in `claude-followup.yml` for the mechanics.
- **`auto-models` label** — applied by `auto-update-models.yml` to both its new-model PR and to its missing-pricing issue.
- **`cost-tracking` label** — applied by `auto-update-models.yml` to its missing-pricing issue.

## Troubleshooting
- **Run fails immediately in the `claude-code-action` step** — check that `ANTHROPIC_API_KEY` is set and valid; that's the most common cause across all these workflows.
- **`claude-code-review.yml` didn't run on a PR from a fork** — expected, see Forked PR limitations above. There's no failed job to debug; the run was skipped.
- **`auto-update-models.yml`'s `reconcile` job fails** — check its header comment's `Requirements:` line for which secret it needs, and confirm it's set and valid.
- **A Claude-created PR didn't get a second follow-up round** — by design, see the `claude-followup-done` label above. Remove the label, or comment `@claude` on the PR, to trigger another pass.
- **Output looks incomplete, or a step Claude should have taken didn't happen** — check the run transcript for denied tool calls, see Required secrets and permissions above.
- **Output quality needs improvement** — comment `@claude` on the issue or PR with what to revise, or update the relevant prompt in the workflow file if the issue is systemic.
