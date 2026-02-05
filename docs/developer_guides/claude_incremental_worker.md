# Claude Incremental Worker

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

## Tasks
- [ ] First task to complete
- [ ] Second task to complete
- [x] Already completed task
- [ ] blocked: Task that can't be done yet - explain why

## Context
Optional background info the AI should know about.

## Learnings
<!-- AI updates this section with discoveries -->
```

## How It Works

1. Checks for open PRs with `claude-incremental` label (waits if one exists)
2. Finds the oldest issue with the label that's "In Progress" in the project
3. Claude reads the issue and implements the first unchecked, non-blocked task
4. Claude updates the issue (checks off task, adds learnings)
5. Creates a PR linking to the tracking issue

## Manual Trigger

Run on a specific issue via Actions > Claude Incremental Worker > Run workflow, then enter the issue number.

## Files

- `.github/workflows/claude-incremental.yml` - The workflow
- `docs/plans/2026-02-04-claude-incremental-design.md` - Detailed design document
