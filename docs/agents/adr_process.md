# Architecture Decision Records (ADRs) Process

ADRs live at `docs/adr/` and are rendered into the docs site under Architecture → Decisions. Each ADR captures one decision with context, consequences, and rejected alternatives. ADRs are sequentially numbered (`0001-...`, `0002-...`) and immutable once accepted — reversing a decision means writing a new ADR that supersedes the old one.

Split decisions along the *independent supersession* axis: a choice you would revise on its own earns its own ADR; a choice that exists only as a forced consequence of a bigger decision (e.g. a stub library dictated by the type-checker you chose) is folded into that decision's ADR. Use `extends:` to link related-but-separate ADRs — relatedness alone is not a reason to split.

**Source-doc lifecycle.** Design and spec docs (anywhere under `docs/`) carry a `status` frontmatter field:

- `active` — still evolving; ADR extraction is gated off.
- `stable` — decisions are settled; safe to extract.
- `extracted` — already crystallised into ADRs; the source doc is now an index or has been deleted.

When you finish a design doc and ship the work, flip `status` from `active` to `stable`, then run the extraction skill.

**Extracting ADRs.** Use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`. It walks you through identifying candidate decisions, drafting each ADR, wiring up cross-references, and updating `mkdocs.yml` plus `docs/adr/index.md`. The skill never commits — review the diff yourself.

**Writing an ADR by hand.** Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number), fill it in, append a row to the `docs/adr/index.md` table, and add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.

**Citing an ADR.** Use `ADR-NNNN` as the canonical reference in code comments, PR descriptions, and conversations. Link to the docs site URL for human-readable context.
