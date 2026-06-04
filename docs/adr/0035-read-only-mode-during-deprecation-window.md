# ADR-0035: Read-only mode gates features during the deprecation window

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-04</p>
<p class="adr-meta">Extends: <a href="0034-tiered-feature-deprecation-by-usage-audit.md">ADR-0034</a></p>

## Context

The full deprecation lifecycle (ADR-0034) needs a mechanism to stop new adoption of a feature during its 60-day wind-down without breaking the teams still using it. The platform has team-scoped waffle flags that could grandfather existing users, but flags carry their own lifecycle (registration, per-team enablement, retirement).

## Decision

We will put a deprecated feature into **read-only mode for all teams** in the same release as the deprecation announcement, enforced at the view layer:

- Gate the create/edit views and remove "New"/"Edit" entry points from templates; show the deprecation warning with removal date and migration path instead.
- Leave existing runtime behaviour untouched — configured features keep working for the whole window.
- Keep delete available; it drains usage.
- API write endpoints for the feature return `403` with a deprecation message.
- Where the feature's config is a field inside a larger form, disable the field with a warning rather than removing it, so existing values stay visible.
- No model-layer enforcement and no feature flags.

## Consequences

- New adoption stops immediately for every team; the audit numbers can only shrink.
- No flag to register, enable per-team, or retire afterwards.
- Existing users are unaffected at runtime until removal.
- Enforcement is per-view, so each surface (UI, API, embedded form fields) must be gated individually — easy to miss one.
- Teams migrating off the feature cannot adjust their existing config during the window; their only options are keep-as-is or delete.

## Alternatives considered

- **Grandfather flag enabled for teams with existing usage** → hides the feature from new teams but adds a flag lifecycle to manage and retire.
- **Warn-only until the removal date** → new usage keeps accruing during the window, growing the migration surface.
- **Model-layer enforcement** → risks breaking legitimate runtime saves (sessions, version snapshot copies) that touch the same models.
