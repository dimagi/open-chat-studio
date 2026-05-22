# AI Tool Setup

New to AI-assisted development on this project? Complete this setup first, then follow the [AI development workflow](../developer_guides/ai_development.md).

## Claude Code

This project's AI workflow is built around [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview).

1. Install Claude Code following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).
2. Install the shared workflows and skills from the [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) repository. Refer to that repository for setup instructions and usage guidance. It provides the skills (`brainstorming`, `writing-plans`, `executing-plans`, etc.) that the [AI development workflow](../developer_guides/ai_development.md) depends on.

## Project AI Instructions

The project's [`AGENTS.md`](https://github.com/dimagi/open-chat-studio/blob/main/AGENTS.md) file provides project-specific context that guides AI agent behavior. It covers project architecture, conventions, key file paths, and which docs to consult for specific features. This file is loaded automatically — you do not need to reference it manually.

`CLAUDE.md` is a one-line file that points to `AGENTS.md`. Other tools read `AGENTS.md` directly (see [Other Tools](#other-tools) below).

## Project Configuration

The project includes a `.claude/settings.json` that configures Claude Code's behavior in this repo:

- **Permissions** — pre-approved safe commands (pytest, ruff, git, gh, etc.) so Claude Code can run them without prompting.
- **Hooks** — see the `.claude/hooks/` directory and the [Claude documentation on hooks](https://code.claude.com/docs/en/agent-sdk/hooks).

You do not need to configure these — they are already checked in and active as soon as you open the project in Claude Code.

## Other Tools

Other agentic coding tools (Gemini CLI, Codex CLI, opencode, Aider, Cline, etc.) can follow the same [AI development workflow](../developer_guides/ai_development.md).

### For Gemini CLI
Google's CLI agent; reads GEMINI.md natively. Create a symlink named for your tool that points to `AGENTS.md`:

```bash
ln -s AGENTS.md GEMINI.md
```

Add the symlink to `.gitignore` — don't commit it.

### For Codex CLI
OpenAI's open-source CLI agent; reads AGENTS.md natively.

### For other tools
Refer to your tool's documentation on how to specify instruction files.
