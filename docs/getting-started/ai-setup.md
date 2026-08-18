# Setup for AI-Assisted Development

New to AI-assisted development on this project? Complete this setup for your local environment,
then follow the [development workflow](dev-workflow.md).

!!! NOTE
    The core principle of this project's workflow is **design before code**, built around
    [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview). If you don't use Claude
    Code, see the [section below](#other-ai-coding-tools).

## Do I need this?

| Situation | Action |
|-----------|--------|
| You plan to use Claude Code for feature work | **Required** — complete this page, then follow the [development workflow](dev-workflow.md) |
| You want occasional AI assistance and plan to use the [GitHub Claude automation](../developer_guides/claude_github_automation.md), but won't adopt the full workflow | **Optional** — at minimum, [install Claude Code](#install-claude-code); skip the skills plugin setup below |
| You're getting the project running, fixing a quick bug, or don't plan to use AI tooling | **Skip for now** — nothing here is needed to run the project or contribute code |

## Install Claude Code

1. Purchase a Claude subscription.
2. Install Claude Code by following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).

## Install the worktree and dev-server tooling

The [development workflow](dev-workflow.md) assumes two small tools. Neither is required, but the
workflow is noticeably better with both:

| Tool | Why | Install |
|---|---|---|
| [worktrunk](https://worktrunk.dev/) (`wt`) | Per-branch git worktrees with per-branch database, seeded data and dependencies — so several agents can work in parallel | See [worktrunk.dev](https://worktrunk.dev/) |
| [portless](https://www.npmjs.com/package/portless) | Stable `ocs.localhost` URLs instead of port numbers, so parallel worktrees don't collide | `npm install -g portless` |

## Set Up Claude Skills and Plugins

Skills are reusable instruction sets that guide Claude through specific workflows. Plugins are
collections of skills installed from a marketplace. OCS uses both.

### OCS project-specific skills

These are in the `.claude/skills/` folder and are active automatically — no installation needed.

Refer to relevant documentation for details on the skills in this folder.

### The Dimagi marketplace

[dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) is a Claude Code
marketplace of Dimagi plugins, plus a curated set of external ones. The project's
`.claude/settings.json` already registers it, so everything below is one `/plugins` away — there is
no marketplace to add by hand.

#### `dev-utils` — the PR side of the workflow

Already enabled for you by `.claude/settings.json` (and auto-loaded by `claude-code-action` in CI).
It provides the PR-side pieces of the [development workflow](dev-workflow.md):

| | Purpose |
|---|---|
| `/review-plan` | Review a plan across architecture, code quality, tests and performance, before any code is written |
| `/create-pr` | Commit, push and open a PR, filling in the repo's PR template |
| `/iterate-pr` | One pass over the current branch's PR — gather review feedback, fix CI, verify, push, reply to threads. `--dry-run` prints the plan only |

Its [README](https://github.com/dimagi/dimagi-claude-workflows/tree/main/plugins/dev_utils) covers
the rest — `/pr-walkthrough`, `babysit-prs`, `git-rebase`, `audit-dependencies` and others.

#### `superpowers` — design-before-code skills

The OCS [development workflow](dev-workflow.md) follows a design-before-code process.
[Superpowers](https://github.com/obra/superpowers) provides the skills that guide Claude through
each phase — exploring the problem, planning the implementation, executing it, and reviewing the
result. Install it from the same marketplace:

```text
/plugins
```

Then pick `superpowers`. `/plugins` is also how you'd browse the rest of the marketplace.

### Code review

Use Claude Code's **built-in `/code-review`** command. It needs no plugin and takes the working
diff, a PR number, a branch or a path:

```text
/code-review          # report findings
/code-review --fix    # report findings and apply them
```

## What's in the project for Claude-assisted development

OCS ships with instruction files that shape how AI agents work in this codebase. They are already
checked in and active — you don't need to configure them.

### Code Agent instruction files

- **`AGENTS.md`** — the primary instruction file. Covers architecture, conventions, key paths, do/don't rules, and which docs to consult for specific areas.
- **`CLAUDE.md`** — a file named for Claude Code; a one-line file that points to `AGENTS.md`.
- **`VISION.md`** — project philosophy. Available for reference when making architectural decisions.
- **`CONTEXT.md`** — domain glossary for the project's terminology. Agents use it to name things consistently. Referred to by `AGENTS.md`.
- **`docs/agents/domain.md`** — describes how agents use the domain glossary (`CONTEXT.md`) and Architecture Decision Records (`docs/adr/`) when exploring the codebase.

#### Domain guides (`docs/agents/`)

Contextual guides for specific areas of the codebase. `AGENTS.md` tells the agent which file to read and when — they are **not** all loaded at startup, keeping agent context lean while providing depth on demand.

### Claude Code settings

`.claude/settings.json` pre-approves safe commands (pytest, ruff, git, gh, etc.) so Claude Code
runs them without prompting, and configures hooks for session startup — including the environment
setup that runs when you open a session in a fresh worktree.

### Worktree setup (`.config/wt.toml`)

Defines what happens when `wt` creates a worktree: per-branch database and Redis DB, dependency
install, migrations and seed data. See
[What worktree setup does](dev-workflow.md#what-worktree-setup-does).

## Other AI coding tools

Other agentic coding tools (Gemini CLI, Codex CLI, OpenCode, Aider, Cline, etc.) can follow the
same [development workflow](dev-workflow.md). Refer to your tool's documentation for details.

### What works with any AI tool

- **AGENTS.md** — Most AI tools load this automatically.
- **VISION.md and CONTEXT.md** — tool-agnostic; can be referenced by any AI coding tool.
- **Domain guides** (`docs/agents/`) — Contextual guidance for specific code areas. Tool-agnostic.

### What is Claude-specific

- **Skills** — Claude-specific and not available in other AI tools.
- **Safe commands** — `.claude/settings.json` contains pre-approved automation-friendly commands (for tests, linting, version control, file search). Refer to your tool's documentation to configure similar permissions.

### Example: Gemini CLI

Google's CLI agent reads `GEMINI.md` natively. Create a symlink:

```bash
ln -s AGENTS.md GEMINI.md
```

Add the symlink to `.gitignore` — don't commit it.
