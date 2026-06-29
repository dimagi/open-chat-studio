# Setup for AI-Assisted Development

New to AI-assisted development on this project? Complete this setup first for your local environment, then follow the [AI development workflow](../developer_guides/ai_development.md).

!!! NOTE
    The core principle of this project's AI workflow is **design before code**, built around [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview). If you don't use Claude Code, see the [section below](#other-ai-coding-tools).

## Do I need this?

| Situation | Action |
|-----------|--------|
| You plan to use Claude Code for feature work | **Required** — complete this page, then follow the recommended [AI development workflow](../developer_guides/ai_development.md) |
| You want occasional AI assistance and plan to use the [Claude Agents](../developer_guides/claude_code_agent.md), but won't adopt the full workflow | **Optional** — at minimum, [install Claude Code](#install-claude-code); skip the skills plugin setup below |
| You're getting the project running, fixing a quick bug, or don't plan to use AI tooling | **Skip for now** — nothing here is needed to run the project or contribute code |

## Install Claude Code

1. Purchase a Claude subscription.
2. Install Claude Code by following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).

## Set Up Claude Skills and Plugins

### OCS project-specific skills

These are in the `.claude/skills/` folder and are active automatically — no installation needed.

### Superpowers plugin for design-before-code skills

The OCS team's recommended [AI development workflow](../developer_guides/ai_development.md) depends on core skills (`brainstorming`, `writing-plans`, `executing-plans`, etc.) provided by the **superpowers** plugin.

Install it from the official Claude plugin marketplace: https://claude.com/plugins/superpowers

### Dimagi plugin skills

The [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) repository contains reusable Dimagi Claude workflows, commands, and configuration. OCS uses the `dev-utils` plugin so its skills (`create-pr`, `iterate-pr`, `review-plan`, etc.) are auto-loaded by `claude-code-action` in CI as well as in local sessions.

The project's `.claude/settings.json` already registers the `dimagi-claude-workflows` marketplace and enables the `dev-utils` plugin automatically — no installation needed.

To enable other recommended plugins, open the [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) repository and follow the setup instructions in `plugins/README.md` for:

- `code-review` — AI code review workflows with specialist agents

## What's in the project for Claude-assisted development

OCS ships with instruction files that shape how AI agents work in this codebase. They are already checked in and active — you don't need to configure them.

### Code Agent instruction files

- **`AGENTS.md`** — the primary instruction file. Covers architecture, conventions, key paths, do/don't rules, and which docs to consult for specific areas.
- **`CLAUDE.md`** — a file named for Claude Code; a one-line file that points to `AGENTS.md`.
- **`VISION.md`** — project philosophy. Available for reference when making architectural decisions.
- **`CONTEXT.md`** — domain glossary for the project's terminology. Agents use it to name things consistently. Referred to by `AGENTS.md`. Created lazily via `/grill-with-docs` as terms get resolved; may not exist yet on a fresh clone.
- **`docs/agents/domain.md`** — describes how agents use the domain glossary (`CONTEXT.md`) and Architecture Decision Records (`docs/adr/`) when exploring the codebase.

#### Domain guides (`docs/agents/`)

Contextual guides for specific areas of the codebase. `AGENTS.md` tells the agent which file to read and when — they are **not** all loaded at startup, keeping agent context lean while providing depth on demand.

### Claude Code settings

`.claude/settings.json` pre-approves safe commands (pytest, ruff, git, gh, etc.) so Claude Code runs them without prompting, and configures hooks for session startup.

## Other AI coding tools

Other agentic coding tools (Gemini CLI, Codex CLI, OpenCode, Aider, Cline, etc.) can follow a similar [AI development workflow](../developer_guides/ai_development.md). Refer to your tool's documentation for details.

### What works with any AI tool

- **AGENTS.md** — Most AI tools load this automatically.
- **VISION.md and CONTEXT.md** — tool-agnostic; can be referenced by any AI coding tool.
- **Domain guides** (`docs/agents/`) — Contextual guidance for specific areas.

### What is Claude-specific

- **Skills** — Claude-specific and not available in other AI tools.
- **Safe commands** — `.claude/settings.json` contains pre-approved automation-friendly commands (for tests, linting, version control, file search). Refer to your tool's documentation to configure similar permissions.

### Example: Gemini CLI

Google's CLI agent reads `GEMINI.md` natively. Create a symlink:

```bash
ln -s AGENTS.md GEMINI.md
```

Add the symlink to `.gitignore` — don't commit it.
