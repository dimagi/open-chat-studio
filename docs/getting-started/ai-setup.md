# AI Tool Setup

New to AI-assisted development on this project? Complete this setup first, then follow the [AI development workflow](../developer_guides/ai_development.md).

## Do I need this?

| Situation | Action |
|-----------|--------|
| You plan to use Claude Code or another AI coding agent for feature work | **Required** — complete this page, then follow the [AI development workflow](../developer_guides/ai_development.md) |
| You want occasional AI assistance but won't adopt the full workflow | **Optional** — at minimum, install Claude Code; skip the dimagi-claude-workflows plugins |
| You're getting the project running, fixing a quick bug, or don't plan to use AI tooling | **Skip for now** — nothing here is needed to run the project or contribute code |

## Claude Code

This project's AI workflow is built around [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview).

1. Install Claude Code following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).
2. Install the [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) plugin collection, which extends Claude Code with Dimagi-specific workflows and skills.
    - Refer to `plugins/README.md` in that repository for setup instructions, including the `/plugin marketplace add dimagi/dimagi-claude-workflows` command.
    - The collection includes the `code-review` and `dev-utils` plugins, and makes the Superpowers plugin available via its marketplace.
    - Superpowers provides the skills (`brainstorming`, `writing-plans`, `executing-plans`, etc.) that the [AI development workflow](../developer_guides/ai_development.md) depends on.

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
