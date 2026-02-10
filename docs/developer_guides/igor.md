# Igor

A GitHub Action that automatically makes incremental progress on large projects by working through tracking issues with task checklists.

## Use Cases

- Migrate JS files to ES modules
- Add TypeScript types across a codebase
- Refactor a large module piece by piece
- Any project that can be broken into independent tasks

## How to Use

1. **Create a tracking issue** with the `claude-incremental` label
2. **Add it to the project** at https://github.com/orgs/dimagi/projects/3
3. **Set status to "In Progress"** when ready for the worker to pick it up

The workflow runs daily at 2am UTC and can be triggered manually.

## Issue Format

```markdown
## Goal
Brief description of what the project aims to achieve.

## Context
Optional background info the AI should know about.

## Tasks

### Task 1: Short description
- [ ] Task 1

Detailed context for this task. Include relevant file paths, expected
behavior, edge cases, or links to related code. This helps the AI
understand scope and intent beyond the one-line summary.

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

Each task gets its own section with a checkbox and a context block. The checkbox is what the workflow uses to track progress — keep it on its own line. The surrounding text provides the AI with the detail it needs to implement the task correctly.

## How It Works

1. Finds the oldest issue with the `claude-incremental` label
2. Skips any issue that already has an open PR (one PR per issue at a time)
3. Claude reads the issue and implements the first unchecked, non-blocked task
4. Claude updates the issue (checks off task, adds learnings)
5. Creates a PR linking to the tracking issue

Multiple issues can have open PRs simultaneously — the constraint is one open PR per issue, not one globally.

## Manual Trigger

Run on a specific issue via Actions > Igor > Run workflow, then enter the issue number.

## Files

- `.github/workflows/claude-incremental.yml` - The workflow
- `docs/plans/2026-02-04-claude-incremental-design.md` - Detailed design document
