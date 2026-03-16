---
name: core-workflow-testing
description: Use when regression testing Open Chat Studio core user workflows via Playwright browser automation
allowed-tools: Bash(playwright-cli:*)
disable-model-invocation: true
argument-hint: "[server-port]"
---

# Core Workflow Testing

Run each workflow defined in the `workflows/` folder using the playwright-cli skill. The server is running on port $0


## Execution

### Parallel execution with subagents

Workflows in the `team_user` category (i.e. `workflows/team_user.md`) must be run in parallel using subagents. Each numbered workflow section (e.g. "1. Authentication", "2. Team Management", etc.) should be dispatched as a separate subagent running concurrently. Each sub-agent should use a different session, by using the `-s=<session-name>` flag when opening the browser.

Each subagent must return a report in this format:

```
Status: <SUCCESS | FAILED>
Comment: <if something failed, describe the failure>
```

After all subagents complete, aggregate their results into the summary report below.

## Report Format

After all workflows have been executed, output a summary report:

```
Status: <SUCCESS | FAILED>
Workflow 1 heading: <SUCCESS | FAILED>
Workflow 2 heading: <SUCCESS | FAILED>
...
```

## Do

- Run `team_user` workflows in parallel using subagents
- Output the summary report with each workflow name and its status

## Don't

- Try to run the server. Assume it is already running. If not, rather exit
- Run `team_user` workflows sequentially — always use parallel subagents
