# Creating Architecture Decision Records

Architecture Decision Records (ADRs) are the project's institutional memory for significant technical choices — immutable documents recording what was decided, why, what alternatives were rejected, and what consequences follow.
View the list of [Architecture Decisions](https://developers.openchatstudio.com/adr/).

Refer to [`agents/adr_process`](../agents/adr_process.md) for the detailed process AI agents follow to create ADRs from design documents.

There are skills (`/extract-adrs`, `/extract-adrs-ci`) that extract ADRs from design docs on request or automatically in CI, so you should not need to write an ADR from scratch, but here are the steps:

## Writing an ADR by Hand

1. Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number).
2. Fill your ADR details into your new file.
3. Append a row to the `docs/adr/index.md` table.
4. Add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.
