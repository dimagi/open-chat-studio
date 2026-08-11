# Creating Architecture Decision Records

See [`agents/adr_process`](../agents/adr_process.md) for what an ADR is and the source-doc lifecycle. View the list of [Architecture Decisions](https://developers.openchatstudio.com/adr/).

There is an [AI agent skill that extracts ADRs](#manually-extract-adrs-with-ai-agent) from design docs on request or automatically in CI, so you should not need to write an ADR from scratch, but here are the steps:

## Writing an ADR by Hand

1. Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number).
2. Fill your ADR details into your new file.
3. Append a row to the `docs/adr/index.md` table.
4. Add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.

## Manually extract ADRs with AI agent

If you have a design document that you want to work with the AI agent to extract the ADRs for your design, use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`.
