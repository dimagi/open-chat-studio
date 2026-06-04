# ADR-0032: Tiered feature deprecation gated by a usage audit

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-04</p>

## Context

The platform has accumulated features superseded by newer ones (external survey links, the iframe `/embed` public-chat endpoint), with no process for retiring them. A uniform wind-down ceremony is too heavy for dead features, while ad-hoc removal risks breaking teams that still depend on a feature. Deprecations also interact with versioned models: feature config lives on immutable historical version rows, so data removal is not a simple column drop.

## Decision

We will run every feature deprecation through a usage audit that routes it onto one of two paths, tracked in a GitHub issue created from the `feature_deprecation` issue template.

- **Stage 0 — usage audit**: measure *configured* (teams with the feature set up) and *active* (usage events) over a 90-day lookback. A feature with zero active usage is **unused** — configured-but-dormant counts as unused, since dormant config is data to clean up, not usage to migrate.
- **Fast path (unused)**: announce in the changelog/docs, then remove code in the next release. No grace period beyond the release gap.
- **Full lifecycle (used)**: announce on all comms channels with a removal date at least 60 days out, put the feature into read-only mode (ADR-0033), and support migration during the window. Removal requires **both** the date passing **and** every remaining active team triaged — contacted/migrated or breakage explicitly accepted by the feature owner. The date is a checkpoint, not a hard cutoff.
- **Two-phase drop (both paths)**: release N removes UI and business logic but keeps schema; a later release deletes config rows — including on historical version rows — then drops columns/tables. The field-audit log retains the removed values.

## Consequences

- Dormant features can be removed quickly instead of inheriting a 60-day ceremony.
- Actively-used features cannot be removed silently: the triage requirement makes remaining breakage an explicit owner decision.
- A single holdout team cannot block removal indefinitely.
- Every deprecation costs an up-front audit script and a tracking issue.
- Removal spans two releases, and the first release leaves orphaned schema behind temporarily.
- Historical chatbot versions lose fidelity for removed features; the audit log is the only record.

## Alternatives considered

- **Uniform lifecycle for all features** → heavyweight ceremony for features nobody uses.
- **Per-feature judgment with no default stages** → re-litigates sequencing on every deprecation.
- **Hard removal date** → silently breaks teams that missed the comms window.
- **Removal blocked until usage drains to zero** → one holdout stalls removal indefinitely.
- **Single-release code + schema drop** → migration is not deploy-safe and there is no recovery window.
- **Export config before dropping** → no consumer for the archive; audit log already retains values.
