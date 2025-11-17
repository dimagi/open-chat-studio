# Claude Code Development Workflow

This project uses Claude Code with specialized slash commands for structured development. This approach follows [advanced context engineering principles](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/) using commands from [dimagi-claude-plugins](https://github.com/dimagi/claude-plugins).

## Five-Step Development Flow

### 1. Research the Codebase

**Command:** `/research_codebase`

Before starting work, explore the codebase to find relevant code, patterns, files, and architecture related to your task.

**Output:** Documentation written to `docs/claude/research/`

**Purpose:** Build context about existing implementations, coding patterns, and related functionality before planning changes.

### 2. Create Implementation Plan

**Command:** `/create_plan`

Based on the task requirements and research findings, create a detailed implementation plan.

**Output:** Plan written to `docs/claude/plans/`

**Purpose:** Define the approach, identify files to modify, and outline implementation steps before writing code.

### 3. Iterate on the Plan

**Command:** `/iterate_plan`

Refine the plan based on feedback, additional research, or changed requirements.

**Purpose:** Ensure the plan is comprehensive and accurate before implementation begins.

### 4. Implement the Plan

**Command:** `/implement_plan`

Execute the plan by writing code, running tests, and making the planned changes.

**Purpose:** Systematically implement the solution following the validated plan.

### 5. Validate Implementation

**Command:** `/validate_plan`

Verify that the implementation meets the plan's requirements and success criteria.

**Purpose:** Confirm the implementation is complete and correct before considering the task done.

## Benefits

- **Reduced errors:** Research and planning before coding prevents architectural mistakes
- **Better context:** Claude understands the codebase before making changes
- **Traceable decisions:** Plans and research are documented for future reference
- **Systematic approach:** Structured workflow ensures nothing is missed
- **Easier reviews:** Documented plans make code reviews more focused

## Tips

- Use `/research_codebase` even for small tasks to understand existing patterns
- Iterate on plans when requirements are unclear or complex
- Reference research documents when asking questions about implementation
- Keep plans focused on a single feature or fix

## Local Environment Setup

To use these slash commands in Claude Code, install the required plugin:

```
/plugin marketplace add dimagi/claude-plugins
/plugin install research-plan-build
```
