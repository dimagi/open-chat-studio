# Claude Workflows

GitHub Actions workflows for automated issue implementation, incremental task progress, and CI followup.

## Use Cases

- Implement an issue end-to-end when labeled or mentioned
- Work through multi-task issues incrementally (one task per run)
- Automatically fix CI failures and address review comments on Claude PRs

## How to Use

### One-off: label an issue

1. Apply the `claude` label to an issue
2. Claude creates a branch, implements the work, and opens a PR

### Incremental: multi-task issues

1. **Create a tracking issue** with the `claude` label using the format below
2. The workflow runs daily at 2am UTC and picks the oldest eligible issue
3. Each run implements one unchecked task and creates a PR
4. After the PR merges, the next run picks up the next task
5. Can also be triggered manually via Actions > Claude Code > Run workflow

### Interactive: @claude in comments

Mention `@claude` in any issue or PR comment to get a response or request changes.

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

After Claude creates a PR, the **Claude Followup** workflow automatically runs one round of fixes when CI completes:

1. Waits for the "Lint and Test" workflow to complete on any `claude/**` branch
2. Checks for the `claude-followup-done` label — if present, skips (one-round limit)
3. Reads CI failure logs and review comments
4. Fixes lint, type, test, and lockfile issues
5. Addresses actionable review feedback
6. Pushes fixes and comments on the PR with a summary
7. Adds the `claude-followup-done` label to prevent re-runs

## Manual Trigger

Run on a specific issue via Actions > Claude Code > Run workflow, then enter the issue number.

## Files

- `.github/workflows/claude.yml` — Main workflow (event-driven + scheduled)
- `.github/workflows/claude-followup.yml` — Automatic CI followup
