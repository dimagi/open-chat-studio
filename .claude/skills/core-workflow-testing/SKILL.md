---
name: core-workflow-testing
description: Use when regression testing Open Chat Studio core user workflows via Playwright browser automation
allowed-tools: Bash(playwright-cli:*)
disable-model-invocation: true
argument-hint: "[server-port] [optional: comma-separated list of failing test names]"
---

# Core Workflow Testing

Run workflows defined in the `playwright/workflows/` folder (relative to the repo root) using the playwright-cli skill. The server is running on port $0.

If a list of failing tests is provided as the second argument (e.g. `"02-create-and-test-chatbot.spec.ts > Chatbot Management > Create a chatbot, 03-evaluations-datasets-annotations.spec.ts > Evaluations > Run evaluation"`), extract the unique workflow sections from that list using the following explicit mapping:

| Spec filename | Workflow section |
|---|---|
| `01-create-and-test-chatbot.spec.ts` | Flow 1: Create and Test a Chatbot |
| `02-evaluations-datasets-annotations.spec.ts` | Flow 2: Evaluations, Datasets, and Annotations |

Run **only** those sections instead of all workflows.

If no test list is provided, run all workflows.

## Execution

### Parallel execution with subagents

Workflows in the `team_user` category (i.e. `playwright/workflows/team_user.md`) must be run in parallel using subagents. Each numbered workflow section to be tested should be dispatched as a separate subagent running concurrently. Each sub-agent should use a different session, by using the `-s=<session-name>` flag when opening the browser.

Each subagent must return a report in this format:

```
Status: <SUCCESS | FAILED>
Comment: <if something failed, describe the failure>
```

After all subagents complete, aggregate their results into the summary report below.

## Report Format

After all workflows have been executed, output a summary report. When all tests passed, the final line should say "STATUS: PASSED", otherwise it should say "STATUS: FAILED". Example report:

```
Workflow 1 heading: <SUCCESS>
Workflow 2 heading: <FAILED>
Workflow 3 heading: <SUCCESS>
...

STATUS: FAILED
```

## Do

- Run `team_user` workflows in parallel using subagents
- Output the summary report with each workflow name and its status

## Don't

- Try to run the server. Assume it is already running. If not, rather exit
- Run `team_user` workflows sequentially — always use parallel subagents
