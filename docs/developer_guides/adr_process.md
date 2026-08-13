# Creating Architecture Decision Records

See [`agents/adr_process`](../agents/adr_process.md) for what an ADR is and the source-doc lifecycle. View the list of [Architecture Decisions](../adr/index.md).

An AI agent skill can extract ADRs from a design doc, either on request or automatically in CI, so you shouldn't often need to write one from scratch. Both paths are below, along with what stays a manual decision either way.

## Manually extract ADRs with AI agent

If you have a design document that you want to work with the AI agent to extract the ADRs for your design, use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`.

## Writing an ADR by hand

1. Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number).
2. Fill your ADR details into your new file.
3. Append a row to the `docs/adr/index.md` table.
4. Add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.

## Your responsibilities

The skills automate drafting, formatting, cross-referencing, and code verification, but a few calls stay with you:

* **Deciding a design doc is settled.** Flipping `status` from `active` to `stable` is what unblocks extraction — see the [source-doc lifecycle](../agents/adr_process.md#source-doc-lifecycle). Nothing extracts ADRs from a doc you haven't marked stable.
* **Making the editorial calls inside an interactive `/extract-adrs` run.** Which candidates are real decisions, merges/splits, each ADR's status, the `Extends:` graph — the skill stops and asks; see its gate/decision logic in `.claude/skills/extract-adrs/SKILL.md`.
* **Reviewing and committing.** The interactive skill never commits; the CI variant (`.claude/skills/extract-adrs-ci/SKILL.md`) opens a PR but never merges. A human is the only path to `main` either way.
* **Confirming before editing an already-`accepted` ADR.** Reversing a decision means writing a new superseding ADR, not rewriting the old one — called out in the repo's `AGENTS.md` "Ask first" list.
* **Citing ADRs and flagging conflicts while designing new work.** See [`agents/domain`](../agents/domain.md#cite-adrs).
