# Claude Incremental Worker

A GitHub Action that runs daily to make incremental progress on large projects by working through tracking issues with checklists.

## Use Cases

- Migrate JS files to ES modules
- Add TypeScript types to a codebase
- Refactor a large module piece by piece
- Add documentation to multiple files
- Any large project that can be broken into independent steps

## How It Works

1. **Daily at 2am UTC** (or manual trigger), the workflow runs
2. Checks for open PRs with `claude-incremental` label - if found, exits (waiting for review)
3. Finds the oldest open issue with `claude-incremental` label
4. Parses the issue to find the first unchecked, non-blocked task
5. Runs Claude Code to implement that task
6. Creates a PR linking to the tracking issue
7. Updates the tracking issue: checks off the completed task

## Creating a Tracking Issue

Add the `claude-incremental` label to any issue that follows this format:

### Required Format

```markdown
## Goal
[One paragraph describing the overall objective]

## Tasks
- [ ] First task to complete
- [ ] Second task to complete
- [ ] Third task to complete

## Context
[Optional: Background information, patterns to follow, files to reference]

## Learnings
[Leave empty - the worker will add learnings here as it completes tasks]
```

### Task Format Rules

| Format | Meaning |
|--------|---------|
| `- [ ] Task description` | Pending task (will be picked up) |
| `- [x] Task description` | Completed task (skipped) |
| `- [ ] blocked: Task description` | Blocked task (skipped until unblocked) |

### Example Tracking Issue

```markdown
## Goal
Migrate all JavaScript files in assets/javascript/dashboard to ES modules format.

## Tasks
- [ ] Convert charts.js to ES modules
- [ ] Convert main.js to ES modules
- [ ] Convert filters.js to ES modules
- [ ] Update webpack config for dashboard ES modules output

## Context
We're using the modules/ output path. See webpack.config.js modulesConfig for the pattern.

Follow the conversion pattern used in assets/javascript/utils/ which was already migrated.

Import paths must use .js extension even for TypeScript files.

## Learnings
```

## Template for Agents

When creating a tracking issue, use this template:

```markdown
## Goal
[Describe the project objective in 1-2 sentences. Be specific about scope.]

## Tasks
[List 5-15 independent tasks. Each should be completable in a single PR.]
- [ ] [Specific, actionable task 1]
- [ ] [Specific, actionable task 2]
- [ ] [Specific, actionable task 3]

## Context
[Include any of the following that are relevant:]
- Files or directories to reference for patterns
- Coding conventions to follow
- Dependencies or prerequisites
- Links to related documentation

## Learnings
[Leave this section empty - the worker will populate it]
```

### Tips for Writing Good Tasks

1. **Be specific**: "Convert dashboard/charts.js to ES modules" not "Convert some files"
2. **Keep tasks independent**: Each task should be completable without the others
3. **Include file paths**: Reference specific files when possible
4. **Avoid dependencies**: If task B requires task A, note it with `blocked:` prefix
5. **Right-size tasks**: Each task should result in a single, reviewable PR

## Manual Trigger

To run the workflow manually:

1. Go to Actions → "Claude Incremental Worker"
2. Click "Run workflow"
3. Optionally specify an issue number to work on a specific issue

## Workflow Behavior

| Scenario | Behavior |
|----------|----------|
| Open PR with label exists | Exits - waiting for PR review |
| No issues with label | Exits - no work to do |
| All tasks complete | Closes issue, tries next oldest issue |
| Task cannot be completed | Comments on issue, marks task as blocked |
| All remaining tasks blocked | Comments on issue, exits |

## Files

- `.github/workflows/claude-incremental.yml` - Main workflow
- `scripts/claude-incremental/parse-issue.js` - Parses issue body
- `scripts/claude-incremental/find-next-task.js` - Finds next task
- `scripts/claude-incremental/update-issue.js` - Updates issue via GitHub API
