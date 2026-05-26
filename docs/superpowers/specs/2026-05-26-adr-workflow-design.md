---
status: active
---

# ADR Workflow Design

## Context

Open Chat Studio accumulates architectural decisions across three docs directories:

- `docs/design/` — large multi-decision design documents (e.g. `unified-assessment.md`, 823 lines; `email_channel.md`, 402 lines).
- `docs/plans/` — a mix of design-flavoured plans and pure implementation plans (e.g. `channels_refactor.md`, 2969 lines; `channels_refactor_testing_plan.md`).
- `docs/superpowers/specs/` — newer per-feature specs produced by the `brainstorming` skill (currently 11 files, March–May 2026).

These documents are difficult to reference precisely. A decision is buried inside an 800-line narrative; there's no canonical identifier to cite in code comments, PRs, or Slack; and when a decision is later revised or reversed, there's no clean way to record the change. `AGENTS.md` already names `docs/adr/` as the intended location for Architecture Decision Records but the convention is unstarted.

The site is built with Zensical (`zensical>=0.0.40`, `.github/workflows/docs.yaml` runs `zensical build --clean`), which is largely compatible with `mkdocs-material` and uses the same `mkdocs.yml`. The local dev server still runs `mkdocs serve` via `tasks.py`.

## Decision

Adopt a sequential, MADR-flavoured ADR system at `docs/adr/`, rendered through the `mkdocs-material-adr` plugin (with a fallback path if Zensical rejects the custom theme name). Existing stable design documents are extracted into multiple ADRs each via a guided `/extract-adrs` skill, then either shrunk to a pointer block or deleted. Active documents are gated out of extraction by a frontmatter `status` field.

### Architecture overview

Three components, each with a single purpose:

1. **ADR store** — `docs/adr/` directory; one `.md` per decision; sequential numbering; status pill in the rendered header; auto-generated supersedes/extends graph.
2. **Source-doc lifecycle** — every legacy and new design doc carries a `status: active | stable | extracted` field. The extraction skill refuses to operate on `active` docs and offers re-extraction on `extracted` ones.
3. **Extraction skill** — `/extract-adrs <source-doc>` runs a guided conversation: read source → list candidate decisions → confirm each → write ADR files → update source doc → update mkdocs nav → verify with `zensical build --clean`.

The three are independent: the store works without the skill (ADRs can be authored by hand), the skill works without the lifecycle (it just asks per-file if no frontmatter exists), and the lifecycle works without ADRs at all (it's just frontmatter on design docs).

### ADR file format

**Filename:** `0001-kebab-case-title.md`, zero-padded to four digits.

**Frontmatter** (per `mkdocs-material-adr`):

```yaml
---
title: 0042 Unified score table
adr:
  author: <name>
  created: <YYYY-MM-DD>
  status: accepted          # draft | proposed | rejected | accepted | superseded
  superseded_by: 0058-...   # optional
  extends:                  # optional
    - 0040-assessment-as-user-unit
---
```

**Body structure:**

```markdown
## Context
<the forces — why this decision needed to be made; what was true at the time>

## Decision
<one paragraph stating what we decided, in plain language>

## Consequences
<good and bad — what becomes easier, what gets harder, what new constraints exist>

## Alternatives considered
<options that were rejected, with one-line rationale per>
```

No "Implementation notes" or "Open questions" sections. ADRs record the decision; rollout and open questions belong in active design docs.

**Sequential, not date-prefixed:** dates re-sort by happenstance of authorship and create no stable reference. `ADR-0042` is a citation; `2026-05-26-...` is metadata.

### Source-doc lifecycle

Each design or spec doc carries one of three statuses in frontmatter:

| Status | Meaning | Skill behaviour |
|---|---|---|
| `active` | Still evolving; decisions may still change | Refuses to extract |
| `stable` | Decisions are settled; safe to crystallise into ADRs | Proceeds with extraction |
| `extracted` | ADRs already written from this doc | Asks "re-extract?" — rare path |

Docs without frontmatter are classified interactively the first time the skill is run against them. `docs/design/unified-assessment.md` is marked `active` up front so it cannot be accidentally extracted while still in flight.

### Source disposition after extraction

The skill's disposition decision per source doc:

- **Design doc with surrounding narrative (user stories, requirements, backlog mapping)** — shrink to a brief overview plus a bulleted "see also: ADR-0042, ADR-0043, …" pointer block. Set `status: extracted`.
- **Pure implementation plan** — delete the file. The work has shipped; the plan was scaffolding.
- **Mixed** — the skill surfaces both options for the user to choose per-file.

### Extraction skill phases

`/extract-adrs <source-doc>` runs five phases:

**Phase 1 — Gate check.** Read frontmatter. Refuse if `active`. Ask "re-extract?" if `extracted`. If no frontmatter, ask the user to classify (`active`, `stable`, or "pure plan — just delete").

**Phase 2 — Candidate decisions.** Read the source and propose a numbered list of candidate decisions, each with a one-line title and the source section(s) it came from. User edits the list (drop, merge, rename, split) until approved.

**Phase 3 — Per-ADR drafting.** For each candidate, draft the ADR (Context / Decision / Consequences / Alternatives), confirm `status` via `AskUserQuestion` (`accepted` is the default for extraction from shipped designs; `proposed` for not-yet-implemented). Per-ADR confirmation rather than batch — refining the interpretation as it goes prevents the skill from drifting across a long doc.

**Phase 4 — Cross-references.** After all ADRs are drafted, scan for cases where one ADR builds on another; populate `extends:` automatically; show the inferred graph for user confirmation.

**Phase 5 — Write & wire up.**
- Allocate sequential numbers atomically per run (`ls docs/adr/ | tail -n1` + 1 at start of run; not per file, to avoid gaps if the user cancels).
- Write ADR files to `docs/adr/`.
- Update the source doc per the disposition rule.
- Append entries to `mkdocs.yml` under `Architecture > Decisions`.
- Run `uv run --no-project zensical build --clean` to catch broken links and bad frontmatter.
- Print summary: N ADRs written, source disposition, next ADR number for future runs.

**Safety properties:**
- The skill never commits. The user reviews the diff and commits.
- ADR files are written atomically per file; the source doc is touched only at the end.

### Renderer and the spike

`mkdocs-material-adr` requires:
- `theme: name: mkdocs-material-adr` (replaces `theme: name: material`).
- Plugin namespacing — `search` → `material/search`.
- The plugin entry — `mkdocs-material-adr/adr`.

It is unverified whether Zensical accepts the custom theme name. The plugin is a thin wrapper around the Material theme, so all existing palette/navigation features should pass through, but this needs validation.

**Spike (Phase A of rollout):** install the plugin, swap the theme, build a single throwaway ADR (`0000-record-architecture-decisions.md`), and run both `zensical build --clean` and `mkdocs serve`.

- **Both work** → keep the plugin; proceed with the design as written.
- **`mkdocs serve` works, `zensical build` rejects the theme** → fall back to plain `theme: material` + custom CSS status pills + a hand-written ADR index. Every component downstream (skill, lifecycle, numbering, template) is unchanged.
- **Both reject** → same fallback, more confident.

### mkdocs.yml nav

ADRs sit under the existing `Architecture` tab rather than getting their own top-level tab. The current `Architecture` tab has only an "Overview" subpage; co-locating Decisions there gives it more substance without inflating the top nav.

```yaml
nav:
  - Architecture:
    - Overview: architecture/index.md
    - Decisions:
      - Index: adr/index.md
      - 0001 ...: adr/0001-....md
      - 0002 ...: adr/0002-....md
```

The skill maintains this block: when writing new ADRs, it appends entries in numeric order under `Architecture > Decisions`.

`adr/index.md` contains a brief intro plus the `[GRAPH]` marker (when the plugin is in use) or a manual table of ADRs (in the fallback path).

## Consequences

**Good**
- Decisions become individually citable (`ADR-0042`) from code, PRs, and conversations.
- Supersession is first-class — a reversed decision is a new ADR pointing to the old one, not silent text edits.
- The lifecycle field disentangles "still arguing about this" from "this is the decision" without moving files around.
- Large design docs stop being giant unindexed blobs; their per-decision content gains a stable URL.
- Future LLM agents can be pointed at `docs/adr/` for canonical decision context, rather than having to read multi-thousand-line designs.

**Bad / costs**
- Initial extraction is real work: 11 superpowers specs + 2 design docs + a handful of design-flavoured plans, each one requiring per-ADR review. Mitigated by the skill but not eliminated.
- Theme swap risk: if Zensical doesn't accept `mkdocs-material-adr` as a theme name, the fallback path is more manual (CSS pills, hand-written index).
- The skill is itself a maintenance surface — if extraction patterns evolve, the skill needs updates.
- Two coexisting source-of-truth conventions during the transition: extracted-and-shrunk docs alongside not-yet-extracted ones. Resolved over time but messy in the interim.

## Alternatives considered

- **One ADR per source doc (rename-and-reformat).** Cheap but produces "decision summary docs" rather than ADRs. Rejected because the goal — individually citable decisions — requires real splitting.
- **Forward-only ADRs.** Set up the system, write new ADRs from here on, leave legacy designs untouched. Rejected because legacy docs already contain settled decisions that benefit most from indexing.
- **Date-prefixed numbering (`2026-05-26-...`).** Rejected — re-sorting on authorship date adds no signal and breaks the "ADR-0042" reference idiom.
- **Top-level "Decisions" tab.** Rejected after looking at the current nav — `Architecture` is currently underweight, and nesting Decisions there improves both sections.
- **Separate directories for active vs stable docs.** Rejected in favour of frontmatter — moving files churns git history and breaks links, and the status info wants to travel with the doc anyway.
- **Documented manual process (no skill).** Rejected because the extraction work compounds across ~18 source docs; consistency requires automation.
- **Python `manage.py extract_adrs` script.** Halfway house: scaffolds files but doesn't decide content. Rejected because the hard part is the per-decision judgement, which the skill handles via conversation — a script would just renumber files.

## Rollout phases

**Phase A — Spike.** Install plugin, theme swap, namespace plugins, write `0000-record-architecture-decisions.md`, build with both Zensical and mkdocs, decide plugin vs fallback. One commit.

**Phase B — Skill scaffolding.** Author `/extract-adrs`. Author `docs/adr/_template.md` (excluded from nav). Add `AGENTS.md` section on the ADR workflow and the source-doc lifecycle. Pilot the skill on `docs/superpowers/specs/2026-03-23-elevenlabs-voice-provider-design.md` (133 lines, single feature, shipped).

**Phase C — Bulk extraction.** Walk through stable docs one at a time. Order: newest → oldest in `docs/superpowers/specs/`, then `docs/design/email_channel.md`, then design-flavoured items in `docs/plans/`. Pure-plan files (`channels_refactor_testing_plan.md`, `channels_refactor_example.py`, `2026-02-18-ty-type-checking-plan.md`) are deleted via the skill's plan-disposition path. `docs/design/unified-assessment.md` is marked `status: active` up front and not touched.

**Phase D — Steady state.** New designs from the `brainstorming` skill land in `docs/superpowers/specs/` with `status: active`. When the feature ships, the author flips status to `stable` and runs `/extract-adrs`. The skill, the lifecycle, and the location are documented in `AGENTS.md` so the convention survives.
