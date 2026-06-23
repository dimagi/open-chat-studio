# ADR-0042: Settings-driven internal team metadata in a JSON field

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-23</p>

## Context

Operators need to attach internal bookkeeping to teams — e.g. the employee responsible for a client ("team owner"). This data is staff-facing, not visible to team members, and the set of fields differs per deployment. We needed somewhere to store it and a way to define the available fields without a schema migration per field or per instance.

## Decision

We will store internal team metadata as a single `SanitizedJSONField` (`Team.metadata`, default `{}`), with the available fields declared by the `TEAM_METADATA_FIELDS` setting — a list of `{"key", "label"}` objects loaded from an env var.

- Fields are free-text; the edit form is built dynamically from the setting.
- The field is added to the audited `TEAM_FIELDS`, so changes are logged.
- Saving merges submitted values into `metadata`, preserving keys not in the current setting.
- A staff-only (`is_staff`) page under the team settings (`single_team:internal_metadata`) views and edits the values; the entry button is hidden from non-staff.
- The values are included in admin CSV exports: a dynamic column per field on the existing top-teams export, plus a dedicated all-teams export.

## Consequences

- Adding or renaming a field is a config/env change, not a migration.
- No per-field validation, typing, or referential integrity — a "team owner" is a string, not a FK to a user.
- Removing a field from the setting hides it from the UI/exports but leaves its stored values intact (merge-on-save), so data is recoverable by re-adding the key.
- Field keys are an implicit contract: exports and stored data key off `key`, so renaming a key orphans existing values.

## Alternatives considered

- **Dedicated columns / a TeamMetadata model** → rejected: every new field would need a migration; the field set is instance-specific and low-stakes.
- **Typed or user-reference fields** (e.g. team owner as a FK) → deferred: free-text covers the current need; typed fields can layer on later if notifications or joins are required.
- **Database-backed field definitions** → rejected: instance configuration belongs in settings, consistent with other instance-specific config; no UI to manage definitions is warranted yet.
