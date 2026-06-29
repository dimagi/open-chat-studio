# AI Tool Setup

New to AI-assisted development on this project? Complete this setup first, then follow the [AI development workflow](../developer_guides/ai_development.md).

## Do I need this?

| Situation | Action |
|-----------|--------|
| You plan to use Claude Code or another AI coding agent for feature work | **Required** — complete this page, then follow the [AI development workflow](../developer_guides/ai_development.md) |
| You want occasional AI assistance but won't adopt the full workflow | **Optional** — at minimum, install Claude Code; skip the dimagi-claude-workflows plugins |
| You're getting the project running, fixing a quick bug, or don't plan to use AI tooling | **Skip for now** — nothing here is needed to run the project or contribute code |

## Set Up Claude Code

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

## Project AI Instructions

The project's `AGENTS.md` file provides project-specific context that guides AI agent behavior. It covers project architecture, conventions, key file paths, and which docs to consult for specific features. This file is loaded automatically — you do not need to reference it manually.

`CLAUDE.md` is a one-line file that points to `AGENTS.md`. Some tools read `AGENTS.md` directly, while others require a tool-specific filename (see [Other Tools](#other-tools) below).

## Project Configuration

The project includes a `.claude/settings.json` that configures Claude Code's behavior in this repo:

- **Permissions** — pre-approved safe commands (pytest, ruff, git, gh, etc.) so Claude Code can run them without prompting.
- **Hooks** — see the `.claude/hooks/` directory and the [Claude documentation on hooks](https://code.claude.com/docs/en/agent-sdk/hooks).

You do not need to configure these — they are already checked in and active as soon as you open the project in Claude Code.

## Other Tools

Other agentic coding tools (Gemini CLI, Codex CLI, OpenCode, Aider, Cline, etc.) can follow the same [AI development workflow](../developer_guides/ai_development.md).

### For Gemini CLI
Google's CLI agent; reads GEMINI.md natively. Create a `GEMINI.md` symlink pointing to `AGENTS.md`:

```bash
ln -s AGENTS.md GEMINI.md
```

Add the symlink to `.gitignore` — don't commit it.

### For Codex CLI
OpenAI's open-source CLI agent; reads AGENTS.md natively.

### For other tools
Refer to your tool's documentation on how to specify instruction files.
