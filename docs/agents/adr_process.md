# Architecture Decision Records (ADRs) Process

What an ADR is, and the lifecycle a design doc follows on its way to becoming one.

## What's an ADR

ADRs live at `docs/adr/` and are rendered into the docs site under Architecture → Decisions. Each ADR captures one decision with context, consequences, and rejected alternatives. ADRs are sequentially numbered (`0001-...`, `0002-...`) and immutable once accepted — reversing a decision means writing a new ADR that supersedes the old one.

## Source-doc lifecycle
Design docs produced by the brainstorming skill (`docs/design/`, `docs/superpowers/specs/`) carry a `status` frontmatter field:

- `active` — still evolving; ADR extraction is gated off.
- `stable` — decisions are settled; safe to extract.
- `extracted` — already crystallised into ADRs; the source doc is now an index or has been deleted.

When you finish a design doc and ship the work, flip `status` from `active` to `stable`, then run the extraction skill.

## Creating an ADR

Use the `/extract-adrs <source-doc>` skill to extract ADRs from a stable design doc, or see [Writing an ADR by Hand](../developer_guides/adr_process.md) to author one directly.

## Citing an ADR

See [`agents/domain`](domain.md) for the citation convention and for flagging conflicts with existing ADRs.
