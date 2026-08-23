# GitHub Automation with Claude

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [Github automation with Claude](https://developers.openchatstudio.com/developer_guides/claude_github_automation/).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Depending on the workflow, Claude reads and reviews code, or writes changes, runs tests, and opens or updates PRs based on its instructions.

## Required secrets and permissions

Secret requirements differ per workflow — check the `Requirements:` line in each workflow file's header comment.

Two independent mechanisms scope what Claude can do in every run: the `permissions:` block (what the `GITHUB_TOKEN` can access) and `--allowedTools` in `claude_args` (which tools **run without a prompt for permission**). Getting either wrong is the main way a compromised or malicious prompt — e.g. a hostile issue/PR body — could take unintended action, so treat changes to them as security-sensitive. See [claude-code-action's security docs](https://github.com/anthropics/claude-code-action/blob/main/docs/security.md) and the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) for how they actually behave.

> [!WARNING]
> If Claude tries to use a tool that isn't pre-approved, the call is denied and Claude continues without it — **the run won't fail**. A missing tool usually surfaces as an **incomplete result rather than an error**, so check the run transcript for denied tool calls if the output looks truncated.

## GitHub workflows at a glance

| File | Actions UI name | Trigger |
|---|---|---|
| `claude.yml` | Claude Code | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch |
| `claude-followup.yml` | Claude Followup | CI (i.e. Lint and Test) workflow completes on any `claude/**` branch |
| `claude-dependabot.yml` | Claude Dependabot PR Review | Dependabot PR opened or updated, manual dispatch |
| `claude-code-review.yml` | Claude Code Review | PR opened, marked ready for review, or pushed |
| `auto-update-models.yml` | Auto Update LLM Models | Daily schedule, manual dispatch |

Check the comment block at the top of each file for what it does, when it is skipped and what it needs.

## Forked PR limitations

`claude-code-review.yml` and `claude-followup.yml` both skip fork-originated PRs — see the `Notes:` in each header comment for why.

## Concurrency and run cancellation

Each workflow's concurrency setup follows one of three patterns, chosen by whether an in-progress run can safely be interrupted:

- **Queue per unit of work** — group by the issue, PR, or branch being acted on, and let a second trigger wait rather than replace the run (`cancel-in-progress: false`). Used when the run does work that shouldn't be interrupted mid-flight.
- **Cancel superseded work** — group by PR, but cancel an in-progress run when a new one starts (`cancel-in-progress: true`). Used when the in-progress run's output is about to be invalidated anyway, so letting it finish would waste spend.
- **Serialize globally** — one repo-wide group instead of one per issue/PR, so at most one run of the whole workflow executes at a time, regardless of trigger source. Used when a job mutates shared state that a concurrent run could conflict with.

A workflow can also skip grouping entirely and let every run execute independently, when its runs never touch shared state or overlapping work.

See each workflow's header comment for its specific group key and cancellation setting.

## GitHub labels used by these workflows

- **`claude` label** — the one label shared across workflows: `claude.yml` triggers off it (an issue labelled `claude`) and applies it to PRs it opens; `auto-update-models.yml` also applies it to the new-model PR it opens. So any Claude-authored PR carries the same label regardless of which workflow created it.

For the other labels used, see each workflow's header comment.

## Troubleshooting
- **Run fails immediately in the `claude-code-action` step** — check that `ANTHROPIC_API_KEY` is set and valid; that's the most common cause across all these workflows.
- **`claude-code-review.yml` didn't run on a PR from a fork** — expected, see Forked PR limitations above. There's no failed job to debug; the run was skipped.
- **`auto-update-models.yml`'s `reconcile` job fails** — check its header comment's `Requirements:` line for which secret it needs, and confirm it's set and valid.
- **A Claude-created PR didn't get a second follow-up round** — by design, see `claude-followup.yml`'s header comment for the `claude-followup-done` mechanics. Removing the label doesn't start a run by itself — it only clears the skip for the next "Lint and Test" completion, so push a new commit (or re-run "Lint and Test") after removing it. Commenting `@claude` on the PR works immediately instead, independent of the label.
- **Output looks incomplete, or a step Claude should have taken didn't happen** — check the run transcript for denied tool calls, see Required secrets and permissions above.
- **Output quality needs improvement** — comment `@claude` on the issue or PR with what to revise, or update the relevant prompt in the workflow file if the issue is systemic.
