# Claude Incremental - Automated Incremental Project Worker

## Overview

A GitHub Action that runs daily (and on-demand) to make incremental progress on large projects by working through tracking issues with checklists.

**Use cases:**
- Migrate JS files to ES modules
- Add TypeScript types to a codebase
- Refactor a large module piece by piece
- Any large project that can be broken into independent steps

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                         Daily Run (2am UTC)                      │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                    ┌───────────────────────┐
                    │ Open PR with label?   │──── Yes ───▶ Exit (wait for review)
                    └───────────────────────┘
                                 │ No
                                 ▼
                    ┌───────────────────────┐
                    │ Find labeled issues   │──── None ──▶ Exit (no work)
                    │ in "In Progress"      │
                    │ (oldest first)        │
                    └───────────────────────┘
                                 │
                                 ▼
                    ┌───────────────────────┐
                    │ Parse checklist       │
                    │ Find first unchecked  │──── All done ──▶ Close issue, try next
                    └───────────────────────┘
                                 │
                                 ▼
                    ┌───────────────────────┐
                    │ Run Claude Code       │
                    │ - Implement task      │
                    │ - Lint + fix          │
                    │ - Test + fix          │
                    └───────────────────────┘
                                 │
                        ┌───────┴───────┐
                        │               │
                    Success          Failed
                        │               │
                        ▼               ▼
                    Create PR      Comment on issue
                    Check off      Mark item blocked
                    Add learnings  Try next item
```

## Tracking Issue Format

Issues must:
1. Have the `claude-incremental` label
2. Be in **"In Progress"** status in the [Open Chat Studio project](https://github.com/orgs/dimagi/projects/3)

Format:

```markdown
## Goal
Migrate all JavaScript files in assets/javascript to ES modules format.

## Tasks
- [ ] Convert dashboard/charts.js to ES modules
- [ ] Convert dashboard/main.js to ES modules
- [x] Convert utils/utils.ts to ES modules
- [ ] blocked: Convert legacy.js - requires jQuery UMD
- [ ] Update webpack config to output all as ES modules

## Context
<!-- Optional: Background info the AI should know -->
We're using the modules/ output path. See webpack.config.js modulesConfig.

## Learnings
<!-- AI adds/updates this section -->
- charts.js imports from legacy jQuery - need to keep UMD for those
- Type exports require .js extension in import paths
```

**Key points:**
- First `- [ ]` item (not prefixed with `blocked:`) is the current task
- `- [x]` items are complete
- `blocked:` prefix tells AI to skip that item
- `## Learnings` section accumulates knowledge across runs

## Workflow Configuration

**File:** `.github/workflows/claude-incremental.yml`

**Triggers:**
- Schedule: Daily at 2am UTC
- Manual: workflow_dispatch for on-demand runs (optionally specify issue number)

**Jobs:**
1. `check-pending-pr` - Check for open PRs with `claude-incremental` label → exit if found
2. `find-work` - Find oldest issue with label **and "In Progress" status** in the [Open Chat Studio project](https://github.com/orgs/dimagi/projects/3), fetch title and body
3. `execute` - Run Claude Code with entire issue body, Claude handles task selection and updates
4. `create-pr` - Create PR with label linking to tracking issue

## Claude Code Invocation

The workflow passes the entire issue content to Claude, letting it handle task selection and issue updates:

```
You are working on an incremental project tracked in issue #N.

## Issue Title
<issue title>

## Issue Content
<full issue body>

## Instructions
1. Read the issue and identify the first unchecked task (- [ ]) that is not marked as blocked
2. Implement that task
3. Run lint and tests, fix any issues:
   - Python: ruff check --fix && ruff format
   - JS/TS: npm run lint
4. After completing the task, update the issue using gh issue edit to:
   - Check off the completed task (change - [ ] to - [x])
   - Add any learnings to the ## Learnings section (create if needed)
5. If you cannot complete the task, mark it as blocked (add "blocked:" prefix to the task)
6. Add a comment to the issue summarizing what you did

If all tasks are complete or blocked, comment on the issue explaining the status.
```

**Claude permissions:**
- `gh issue edit` - to update issue body (check off tasks, add learnings)
- `gh issue comment` - to add completion summary
- Standard code editing and linting tools

## Error Handling

| Scenario | Action |
|----------|--------|
| Open PR exists with label | Exit early, log "Waiting for PR #X" |
| No issues with label in "In Progress" | Exit early, log "No active projects" |
| All items checked in issue | Remove label, add completion comment, try next issue |
| Claude can't complete task | Comment with explanation, add `blocked:` prefix to item, try next |
| All remaining items blocked | Comment on issue, exit (needs human help) |
| Lint/tests fail | Claude attempts to fix; only blocked if can't resolve |
| PR creation fails | Log error, don't modify checklist |

## File Structure

```
.github/
└── workflows/
    └── claude-incremental.yml    # Main workflow (self-contained, no helper scripts)
```

The workflow is intentionally simple - Claude handles all the logic for:
- Parsing the issue to find the next task
- Updating the issue to check off completed tasks
- Adding learnings to the issue

## Verification Commands

For this repo (open-chat-studio):
- Python: `ruff check --fix && ruff format`
- JS/TS: `npm run lint`
- Type check: `npm run type-check`
- Tests: `pytest <relevant-test-files>`

## Future: Reusable Action

Once proven in this repo, extract to `dimagi/claude-incremental-action` with configurable inputs:
- `label` - Issue/PR label (default: `claude-incremental`)
- `pre-verify` - Lint/format commands
- `test-command` - Test runner

## Open Questions

None - ready for implementation.
