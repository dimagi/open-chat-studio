# Claude Code Agents

GitHub Actions workflows for autonomous issue implementation, incremental task progress, and CI followup.

These workflows use [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) to run Claude Code inside GitHub Actions. Each agent run gives Claude access to the repository, a shell, and the GitHub CLI. The agent autonomously reads code, writes changes, runs tests, and opens PRs based on its instructions.

**Three agent modes:** one-shot (implements a single issue end-to-end), incremental worker (processes one task from a checklist per daily run), and interactive (responds to direct `@claude` mentions in issues, PR comments, and PR review bodies). A fourth agent — the followup agent — runs automatically after CI completes on any Claude-authored PR.


## Use Cases

- **Label an issue, get a PR** — apply the `claude` label to any GitHub issue and Claude will create a branch, write the code, and open a PR for review
- **Tackle large projects one task at a time** — create a checklist issue and Claude implements one item per day, opening a PR for each so you can review before the next task begins
- **Auto-repair Claude PRs** — after Claude opens a PR, a followup agent watches CI results and reviewer comments, fixes failures, and pushes corrections automatically (one round per PR)
- **Understand Dependabot updates before merging** — when Dependabot opens a dependency PR, Claude reads the changelogs, flags breaking changes, and posts a merge recommendation

## How to Use

### One-shot agent

1. Apply the `claude` label to an issue
2. Claude creates a branch, implements the work, and opens a PR

### Incremental worker agent

1. **Create a tracking issue** with the `claude` label using the format below
2. The agent runs daily at 2am UTC and picks the oldest eligible issue
3. Each run implements one unchecked task and creates a PR
4. After the PR merges, the next run picks up the next task
5. Can also be triggered manually via Actions > Claude Code > Run workflow

### Interactive agent

Mention `@claude` in any issue comment, PR comment, or PR review body to get a response or request changes.

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

After Claude creates a PR, the **Claude Followup Agent** automatically runs one round of fixes when CI completes:

1. Waits for the "Lint and Test" workflow to complete on any `claude/**` branch
2. Checks for the `claude-followup-done` label — if present, skips (one-round limit)
3. Reads CI failure logs and review comments
4. Fixes lint, type, test, and lockfile issues
5. Addresses actionable review feedback
6. Pushes fixes and comments on the PR with a summary
7. Adds the `claude-followup-done` label to prevent re-runs

## Manual Trigger

Run on a specific issue via Actions > Claude Code > Run workflow, then enter the issue number.

## Dependabot PR Review

The **Claude Dependabot PR Review** workflow runs automatically on every Dependabot PR. It:

1. Identifies all changed dependencies and their version ranges
2. Fetches changelogs and release notes for each package
3. Assesses breaking changes and security impact against the OCS codebase
4. Posts a review comment with a risk level (LOW/MEDIUM/HIGH) and a merge recommendation (APPROVE / REVIEW_NEEDED / HOLD)

Can also be triggered manually via Actions > Claude Dependabot PR Review > Run workflow, providing a PR number.

## Files

- `.github/workflows/claude.yml` — Main workflow, named "Claude Code" in the Actions UI (event-driven + scheduled)
- `.github/workflows/claude-followup.yml` — Automatic CI followup
- `.github/workflows/claude-dependabot.yml` — Automatic Dependabot PR review
