# Creating Architecture Decision Records

Architecture Decision Records (ADRs) are the project's institutional memory for significant technical choices — immutable documents recording what was decided, why, what alternatives were rejected, and what consequences follow.
View the list of [Architecture Decisions](https://developers.openchatstudio.com/adr/).

Refer to [`agents/adr_process`](../agents/adr_process.md) for the detailed process AI agents follow to create ADRs from design documents.

There is an [AI agent skill that extracts ADRs](#manually-extract-adrs-with-ai-agents) from design docs on request or automatically in CI, so you should not need to write an ADR from scratch, but here are the steps:

## Writing an ADR by Hand

1. Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number).
2. Fill your ADR details into your new file.
3. Append a row to the `docs/adr/index.md` table.
4. Add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.

## Manually extract ADRs with AI agent

If you have a design document that you want to work with the AI agent to extract the ADRs for your design, then use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`.

It walks you through identifying candidate decisions, drafting each ADR, wiring up cross-references, and updating `mkdocs.yml` plus `docs/adr/index.md`. The skill never commits — review the diff yourself.
