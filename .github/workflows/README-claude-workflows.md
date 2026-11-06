# GitHub Automation with Claude

For engineers responsible for extending, debugging, or operating the Claude workflows. For day-to-day usage, see [Github automation with Claude](https://developers.openchatstudio.com/developer_guides/claude_github_automation/).

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Depending on the workflow, Claude reads and reviews code, or writes changes, runs tests, and opens or updates PRs based on its instructions.

## Required secrets and permissions

Secret requirements differ per workflow — check the `Requirements:` line in each workflow file's header comment.

Two independent mechanisms scope what Claude can do in every run: the `permissions:` block (what the `GITHUB_TOKEN` can access) and `--allowedTools` in `claude_args` (which tools **run without a prompt for permission**). Getting either wrong is the main way a compromised or malicious prompt — e.g. a hostile issue/PR body — could take unintended action, so treat changes to them as security-sensitive. See [claude-code-action's security docs](https://github.com/anthropics/claude-code-action/blob/main/docs/security.md) and the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) for how they actually behave.

> [!WARNING]
> If Claude tries to use a tool that isn't pre-approved, the call is denied and Claude continues without it — **the run won't fail**. A missing tool usually surfaces as an **incomplete result rather than an error**, so check the run transcript for denied tool calls if the output looks truncated.

## GitHub workflows at a glance

| File | Actions UI name | Purpose | Trigger | Prompt |
|---|---|---|---|---|
| `claude.yml` | Claude Code | Implements labelled issues and answers `@claude` requests | Issue labelled `claude`, `@claude` mention, daily schedule, manual dispatch | Inline in the workflow for scheduled runs; the `@claude` comment for event-driven runs |
| `claude-followup.yml` | Claude Followup | One automatic pass to fix CI failures and review feedback on Claude-created PRs | CI (i.e. Lint and Test) workflow completes on any `claude/**` branch | Inline in the workflow |
| `claude-dependabot.yml` | Claude Dependabot PR Review | Summarises dependency changes, changelogs and breaking-change risk | Dependabot PR opened or updated, manual dispatch | Inline in the workflow |
| `claude-code-review.yml` | Claude Code Review | Two-stage PR review (OpenCodeReview, then Claude) plus an advisory merge-risk comment | PR opened, marked ready for review, or pushed | `/code-review:code-review` plugin for the review; `.claude/skills/merge-risk` for the risk comment |
| `auto-update-models.yml` | Auto Update LLM Models | Opens a PR updating the model catalogue and pricing from LiteLLM | Daily schedule, manual dispatch | Inline in the workflow |

The header comment at the top of each file is the source of truth for skip conditions, requirements, concurrency and other details.

### OpenCodeReview stage

The `ocr` job in `claude-code-review.yml` runs [OpenCodeReview](https://github.com/alibaba/open-code-review) before the Claude review. It runs its own CLI, not `claude-code-action`, and is configured through action inputs. It posts findings as inline comments, and the Claude review runs only when it reports none. The `Automated review verdict` check fails if either stage has findings. Both stages share the `ANTHROPIC_API_KEY` secret. Its per-path rules live in `.opencodereview/`.

## Design: workflow, prompt, skill

A Claude run has up to three layers:

- **Workflow**: decides whether to run (triggers, `if:` gates) and sets the limits, namely `permissions:` and `--allowedTools`.
- **Prompt**: the instructions for the run, inline in the workflow file unless the table above says otherwise.
- **Skill or plugin**: optional reusable instructions the prompt invokes. `/merge-risk` is the one repo skill these workflows use, in `.claude/skills/`.

When a run reads untrusted text such as an issue or PR body, limit its tools to reading, plus one output file, and leave any write to GitHub to a later workflow step, as the `merge-risk` job does.

## Fork limitations

Fork PRs get no secrets or OIDC token, so the automatic workflows cannot run Claude on them. Most skip them, leaving no failed run to debug; `claude.yml` fails instead when the `@claude` author lacks write access. See each header comment.

## Concurrency and run cancellation

Each workflow's concurrency setup follows one of these patterns:

- **Queue per unit of work** — group by the issue, PR, or branch being acted on, and don't cancel an in-progress run (`cancel-in-progress: false`). At most one further run waits, and a newer trigger replaces it. Used when the run does work that shouldn't be interrupted mid-flight.
- **Cancel superseded work** — group by PR, and cancel an in-progress run when a new one starts (`cancel-in-progress: true`). Used when the in-progress run's output is about to be invalidated anyway, so letting it finish would waste spend.
- **Serialize globally** — one repo-wide group instead of one per issue/PR, so at most one run of the whole workflow executes at a time, whatever the trigger. Used when a job mutates shared state that a concurrent run could conflict with.
- **No group** — every run executes independently.

See each workflow's header comment for its specific group key and cancellation setting.

## GitHub labels used by these workflows

- **`claude` label**: on an issue, it queues the issue for Claude (labelling triggers `claude.yml`, and the daily run picks the oldest open one). On a PR, it marks Claude-authored work, and `claude.yml` uses it to tell that an issue already has a PR. The scheduled `claude.yml` run and `auto-update-models.yml` apply it to the PRs they open when their prompts say to.

Other labels (`claude-followup-done`, `auto-models`, and the `risk:*` labels set by `pr_risk_label.yml`) are explained in the relevant header comment.

## Troubleshooting
- **Run fails immediately in the `claude-code-action` step** — check that `ANTHROPIC_API_KEY` is set and valid.
- **A workflow didn't run on a PR** — expected for fork PRs (see Fork limitations) and, for `claude-code-review.yml`, Dependabot PRs. The run was skipped, so there is nothing to debug.
- **`claude.yml` fails with "User does not have write access on this repository"** — expected when the `@claude` author has no write access. A maintainer commenting `@claude` works normally.
- **`auto-update-models.yml` fails at "Reconcile against LiteLLM"** — the script exits non-zero when the upstream table is unreadable or implausible. See its header comment.
- **A Claude-created PR didn't get a second follow-up round** — the one-round limit is by design, and a failed pass uses it up. Removing `claude-followup-done` doesn't start a run, so push a commit or re-run "Lint and Test"; commenting `@claude` works immediately.
- **Output looks incomplete, or a step Claude should have taken didn't happen** — check the run transcript for denied tool calls, see Required secrets and permissions above.
- **Output quality needs improvement** — comment `@claude` with what to revise (needs write access), or update the prompt in the workflow file if the issue is systemic.
