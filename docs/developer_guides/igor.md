# Claude Code Agents

GitHub Actions workflows for autonomous issue implementation, incremental task progress, and CI followup.

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Each agent run gives Claude access to the repository, a shell, and the GitHub CLI. The agent autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.


## Use Cases

- **Label an issue, get a PR** — apply the `claude` label to any GitHub issue and Claude will create a branch, write the code, and open a PR for review
- **Tackle large projects one task at a time** — create a checklist issue and Claude implements one item per day, opening a PR for each so you can review before the next task begins
- **Auto-repair Claude PRs** — after Claude opens a PR, CI results and reviewer comments are automatically checked, failures are fixed, and corrections are pushed (one round per PR)
- **Understand Dependabot updates before merging** — when Dependabot opens a dependency PR, Claude reads the changelogs, flags breaking changes, and posts a merge recommendation

## How to Use

### Which workflow to use

| Situation | What to do |
|---|---|
| You have a single, well-scoped issue ready to implement | Apply the `claude` label to the issue |
| You have a larger project with multiple steps you want to review one at a time | Create a checklist issue with the `claude` label |
| You want Claude to answer a question, explain code, or fix something on an open PR | Mention `@claude` in a comment |

### Implementing a single issue

Write the issue clearly — the more specific it is about expected behaviour and relevant files, the better the result. Then:

1. Open the GitHub issue
2. Apply the `claude` label
3. Claude creates a `claude/<issue-number>-<date>` branch, implements the work, and opens a PR with the `claude` label

Review and merge the PR as normal. [Automatic follow-up](#automatic-follow-up) will address any CI failures or review comments on the PR.

### Working through a multi-task project

Use this when you have a larger project — a multi-step refactor, a new feature with several components, or a batch of related tasks — where you want to review each piece before the next one begins.

1. Create a GitHub issue using the [issue format](#issue-format-for-incremental-tasks) below and apply the `claude` label
2. Claude picks up the oldest eligible issue once a day at 2am UTC and implements one unchecked task
3. It opens a PR for that task — review and merge before the next task runs
4. The cycle repeats until all tasks are checked off

To run immediately without waiting for the daily schedule: **Actions > Claude Code > Run workflow**, then enter the issue number.

### Asking or directing Claude with @claude

Use this to ask Claude a question in context, request a specific change to an existing PR, or have Claude address review feedback directly. Mention `@claude` in:

- An **issue comment** — to ask a question or kick off an implementation
- A **PR comment** — to request a specific code change
- A **PR review body** — to have Claude respond to your review feedback

Claude will push changes to the branch or reply in a new comment.

## Issue Format (for incremental tasks)

```markdown
## Goal
Brief description of what the project aims to achieve.

## Context
Optional background info the AI should know about.

## Tasks

### Task 1: Short description
- [ ] Task 1

Detailed context for this task. Include relevant file paths, expected
behavior, edge cases, or links to related code.

### Task 2: Short description
- [ ] Task 2

More context here. The more specific you are about what "done" looks
like, the better the result.

### Task 3: Already completed
- [x] Task 3

### Task 4: Blocked task
- [ ] blocked: Task 4 - explain why

## Learnings
<!-- AI updates this section with discoveries -->
```

Each task gets its own section with a checkbox and a context block. The checkbox is what the workflow uses to track progress — keep it on its own line.

## Automatic Follow-up

After Claude creates a PR, one round of fixes runs automatically when CI completes:

1. Waits for the "Lint and Test" workflow to complete on any `claude/**` branch
2. Checks for the `claude-followup-done` label — if present, skips (one-round limit)
3. Reads CI failure logs and review comments
4. Fixes lint, type, test, and lockfile issues
5. Addresses actionable review feedback
6. Pushes fixes and comments on the PR with a summary
7. Adds the `claude-followup-done` label to prevent re-runs

## Dependabot PR Review

The **Claude Dependabot PR Review** workflow runs automatically on every Dependabot PR. It:

1. Identifies all changed dependencies and their version ranges
2. Fetches changelogs and release notes for each package
3. Assesses breaking changes and security impact against the OCS codebase
4. Posts a review comment with a risk level (LOW/MEDIUM/HIGH) and a merge recommendation (APPROVE / REVIEW_NEEDED / HOLD)

This workflow can also be triggered manually via Actions > Claude Dependabot PR Review > Run workflow, providing a PR number.

## GitHub Workflow Files

- `.github/workflows/claude.yml` — Main workflow, named "Claude Code" in the Actions UI (event-driven + scheduled)
- `.github/workflows/claude-followup.yml` — Automatic CI followup
- `.github/workflows/claude-dependabot.yml` — Automatic Dependabot PR review

## Agent Safety and Tool Boundaries

Each agent run is restricted to an explicit allowlist of tools defined in the `claude_args` field of each workflow file. The agent cannot call anything outside that list. If it needs a tool that isn't permitted, the run fails rather than silently taking an unintended action.
