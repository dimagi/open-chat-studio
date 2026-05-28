# ADR-0000: Record architecture decisions

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

## Context

The project has accumulated significant design and decision content across `docs/design/`, `docs/plans/`, and `docs/superpowers/specs/`, but no single canonical place to record architectural decisions. Decisions are difficult to cite, supersession is invisible, and large design documents bury individual decisions inside surrounding narrative.

## Decision

We will record architectural decisions as Architecture Decision Records (ADRs) at `docs/adr/`, following the [MADR](https://adr.github.io/madr/) format. ADRs are sequentially numbered (`0001-...`, `0002-...`), each captures a single decision, and are immutable once accepted — a revision is a new ADR that supersedes the old one.

The design doc at `docs/superpowers/specs/2026-05-26-adr-workflow-design.md` describes the workflow in detail.

## Consequences

- Decisions become individually citable from code, PRs, and conversations.
- Supersession is first-class: reversed decisions don't silently overwrite history.
- Existing large design docs are extracted into ADRs over time via the `/extract-adrs` skill.
- New design docs (from the brainstorming skill) carry a `status: active | stable | extracted` field so the skill knows which docs are safe to mine.

## Alternatives considered

- **No ADR system, continue with long-form design docs.** Rejected — citation, supersession, and indexing all suffer.
- **Date-prefixed filenames.** Rejected — re-sorts by happenstance of authorship; loses the stable `ADR-NNNN` reference.
- **Forward-only ADRs (no extraction of legacy docs).** Rejected — legacy docs contain the bulk of settled decisions that benefit most from indexing.
- **Use the `mkdocs-material-adr` plugin for rendering.** Rejected — incompatible with Zensical (the docs builder); the plugin's theme extends `mkdocs-material` and expects partials Zensical doesn't expose. Status pills and the index are hand-authored with CSS instead.
