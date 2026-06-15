# Claude Code Agents

This project has GitHub Actions workflows that use Claude Code for [autonomous issue implementation](#1-implementing-a-single-issue), [incremental task progress](#2-working-through-a-multi-task-project), and [CI follow-up](#automatic-follow-up). Additionally, Claude Code is used to review PRs and [Dependabot PRs](#dependabot-pr-review).

## Use Cases

| I want to... | Do this |
|---|---|
| Fix a bug or implement a well-defined feature | [Apply the `claude` label to the issue](#1-implementing-a-single-issue) |
| Understand an issue before Claude starts coding | Comment `@claude draft a plan for this` on the issue |
| Work through a large project one reviewable step at a time | [Create a checklist issue with the `claude` label](#2-working-through-a-multi-task-project) |
| Ask a question, request a specific change, or point Claude at a review comment | [Comment `@claude ...` on the issue or PR](#3-asking-or-directing-claude-with-claude) |


**Automatic behaviours** (no action needed):

- **Auto-repair** — Claude fixes its own CI failures and addresses reviewer comments automatically, so PRs are in a clean state by the time you review them. [How it works](#automatic-follow-up)
- **Dependabot review** — every Dependabot PR includes a Claude-written assessment: what changed, whether anything is breaking, and a clear merge recommendation, saving engineers time. [How it works](#dependabot-pr-review)
- **PR Code Review** — every non-draft, non-fork, non-Dependabot PR automatically receives a Claude code review with findings posted as inline diff comments. [How it works](#pr-code-review)

## How to Use

### 1) Implementing a single issue

Write the issue clearly — the more specific it is about expected behaviour and relevant files, the better the result. Then:

1. Open the GitHub issue
2. Apply the `claude` label
3. Claude creates a `claude/<issue-number>-<date>-<time>` branch (e.g. `claude/123-20240518-143022`), implements the work, and opens a PR with the `claude` label

Review and merge the PR as normal. [Automatic follow-up](#automatic-follow-up) will address any CI failures or review comments on the PR.

### 2) Working through a multi-task project

Use this when you have a larger project — a multi-step refactor, a new feature with several components, or a batch of related tasks — where you want to review each piece before the next one begins.

1. Create a GitHub issue using the [issue format](#issue-format-for-multi-task-projects) below and apply the `claude` label
2. Claude picks up the oldest eligible issue once a day at 2am UTC and implements one unchecked task
3. Claude opens a PR for that task — review and merge before the next task runs
4. The cycle repeats until all tasks are checked off

To run immediately without waiting for the daily schedule: **Actions > Claude Code > Run workflow**, then enter the issue number.

### 3) Asking or directing Claude with @claude

Use this to ask Claude a question in context, request a specific change to an existing PR, or have Claude address review feedback directly. Mention `@claude` in:

- An **issue comment** — to ask a question or kick off an implementation
- A **PR comment** — to request a specific code change
- A **PR review body** — to have Claude respond to your review feedback

Claude will push changes to the branch or reply in a new comment.

**Example prompts:**

| Situation | What to comment |
|---|---|
| You want a plan before any code is written | `@claude draft a plan for this issue` |
| Your PR is missing test coverage | `@claude write unit tests for the changes in this PR` |
| A bot (e.g. Sentry, CodeClimate) left a review comment | `@claude address the review comment from <bot name>` |
| You want to understand what a file or function does | `@claude explain how X works` |
| CI is failing and you want Claude to investigate | `@claude the CI is failing — can you diagnose and fix it?` |

## Issue Format for multi-task projects

```markdown
## Goal
Brief description of what the project aims to achieve.

## Context
Optional background info Claude should know about.

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
<!-- Claude updates this section with discoveries -->
```

Each task gets its own section with a checkbox and a context block. Claude uses the checkbox to track progress — keep it on its own line.

## Automatic Workflows

### Automatic Follow-up

After Claude opens a PR, one round of fixes runs automatically when the **Lint and Test** workflow completes — whether it passed or failed. This applies only to `claude/**` branches — it does not trigger on regular developer PRs.

What you'll see:

- If CI failed, Claude fixes lint, type, test, and lock file issues and pushes a commit
- If there are open review comments, Claude addresses actionable ones
- A summary comment is added to the PR describing what was changed, or confirming no changes were needed
- The `claude-followup-done` label is applied unconditionally to prevent a second round

**One round only.** If CI still fails after the follow-up, address the remaining issues manually or trigger a new run via `@claude` in a PR comment.

### Dependabot PR Review

This workflow runs automatically on every Dependabot PR. It:

1. Identifies all changed dependencies and their version ranges
2. Fetches changelogs and release notes for each package
3. Assesses breaking changes and security impact against the OCS codebase
4. Posts a review comment with a risk level (LOW/MEDIUM/HIGH) and a merge recommendation (APPROVE / REVIEW_NEEDED / HOLD)

This workflow can also be triggered manually via **Actions > Claude Dependabot PR Review > Run workflow**, providing a PR number.

### PR Code Review

Every non-draft, non-fork, non-Dependabot PR triggers an automated Claude code review. Claude reads the PR diff and posts findings as **inline comments** directly on the changed lines.

There is no manual trigger for this workflow. To get a fresh review, push a new commit.

## Maintaining These Workflows

For engineers responsible for extending, debugging, or operating the Claude workflows, see `README-claude-workflows.md` in the repository's `.github/workflows` directory on GitHub.
