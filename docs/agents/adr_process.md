# Architecture Decision Records (ADRs) Process

How architectural decisions get recorded, written, and cited in this project.

## What's an ADR

ADRs live at `docs/adr/` and are rendered into the docs site under Architecture → Decisions. Each ADR captures one decision with context, consequences, and rejected alternatives. ADRs are sequentially numbered (`0001-...`, `0002-...`) and immutable once accepted — reversing a decision means writing a new ADR that supersedes the old one.

## Source-doc lifecycle
Design docs produced by the brainstorming skill (`docs/design/`, `docs/superpowers/specs/`) carry a `status` frontmatter field:

- `active` — still evolving; ADR extraction is gated off.
- `stable` — decisions are settled; safe to extract.
- `extracted` — already crystallised into ADRs; the source doc is now an index or has been deleted.

When you finish a design doc and ship the work, flip `status` from `active` to `stable`, then run the extraction skill.

## Extracting ADRs

Use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`. It walks you through identifying candidate decisions, drafting each ADR, wiring up cross-references, and updating `mkdocs.yml` plus `docs/adr/index.md`. The skill never commits — review the diff yourself.

## Citing an ADR

Use `ADR-NNNN` as the canonical reference in code comments, PR descriptions, and conversations. Link to the docs site URL for human-readable context.
