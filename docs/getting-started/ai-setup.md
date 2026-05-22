# AI Tool Setup

This project has AI-assisted development explained here: [AI development](../developer_guides/ai_development.md)

## Claude Code

1. Install Claude Code following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).
2. Install the shared workflows from the [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows) repository.
3. The project's `CLAUDE.md` and `AGENTS.md` files provide project-specific context automatically.

## Other Tools

If you use a different agentic coding tool, create a symlink so it picks up the project instructions:

```bash
ln -s CLAUDE.md GEMINI.md  # or AGENTS.md, depending on your tool
```

Add the symlink to `.gitignore` — don't commit it.
