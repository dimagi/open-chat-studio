---
status: active
---

# Feature deprecation and removal process

## Background and motivation

OCS has accumulated features that are no longer the recommended way to do
things — surveys (external survey links on experiments) and the old iframe
`/embed` public-chat endpoint (superseded by the StencilJS chat widget) are the
first two candidates. There is no documented process for retiring a feature:
how to measure usage, communicate to affected teams, gate access, handle data
on versioned models, or sunset public endpoints.

This spec designs that process. It produces **documentation only** — a
developer guide — not the execution of any specific deprecation. Surveys and
`/embed` are motivating test cases and will be executed later as separate work,
each following the new guide.

## Deliverable

A new developer guide at `docs/developer_guides/feature_deprecation.md`:

- Added to the mkdocs nav alongside the other developer guides.
- Listed in `AGENTS.md` under "Additional notes":
  `docs/developer_guides/feature_deprecation.md` — when deprecating or removing
  a feature.

Each deprecation is tracked as a GitHub issue on `dimagi/open-chat-studio`,
created by copy-pasting the relevant checklist from the guide. The issue is the
single source of truth for that feature's dates, audit results, and stage
progress. The guide provides a front-matter block for the issue: feature name,
surfaces affected (UI / models / endpoints), replacement (if any), audit date
and result, announced removal date.

## Guide structure

Stage 0 (usage audit) routes each deprecation onto one of two checklists — the
**fast path** (unused) or the **full lifecycle** (used) — followed by shared
reference sections documenting the mechanics once. Checklists are written to be
copy-pasted into the tracking issue.

### Stage 0 — usage audit (always)

Every deprecation starts with a usage audit, recorded on the tracking issue:

- **In-app features**: a throwaway Django shell script measuring, per team,
  (a) *configured* — teams/objects with the feature set up, and (b) *active* —
  usage events within the lookback window.
- **HTTP surfaces**: request counts per endpoint from server logs/metrics over
  the same window, attributed to teams/referrers where derivable.

Default lookback window: **90 days**.

**Tier definition**: a feature is **unused** if there is zero *active* usage in
the window. Configured-but-dormant counts as unused — dormant config is data to
clean up, not usage to migrate. Anything else is **used** and takes the full
lifecycle. The audit script is attached to the tracking issue (gist or pasted),
not committed to the repo.

### Fast path (unused features)

1. **Announce** — changelog entry plus a deprecation note in user docs. No
   banner/email/notification blast. The announcement names the removal release
   and tells anyone affected how to object (support channel / GitHub issue).
2. **Remove immediately** — next release: delete UI entry points, views, URLs,
   forms, business logic. Models/columns stay (phase 1 of the two-phase drop).
   Dormant config rows are left in place at this stage.
3. **Schema drop** — a following release: data migration cleans up dormant
   config rows, then drops columns/tables. Checklist item confirms the
   migration is backwards-compatible (PR template checkbox).
4. **Close out** — remove docs pages (or replace with a tombstone note), close
   the tracking issue.

No grace period beyond the natural gap between releases.

### Full lifecycle (used features)

1. **Announce + comms blast** (day 0) — all channels at once: changelog/docs
   note, targeted banner, in-product notification to affected teams, email to
   admins of affected teams listing *their* specific configs and the removal
   date, and in-feature warnings on the feature's own pages. Removal date =
   **announcement + 60 days** minimum.
2. **Read-only mode** (same release as the announcement) — creating new configs
   and editing existing ones is blocked for all teams; existing configs keep
   functioning. The in-feature warning explains why and links the migration
   path.
3. **Deprecation window** (60 days) — support migration; re-run the audit
   script periodically and note progress on the tracking issue.
4. **Removal checkpoint** (date passed) — re-run the audit. Remaining active
   usage is triaged team-by-team: contacted/migrated, or breakage explicitly
   accepted by the feature owner, recorded on the issue. Removal proceeds only
   when every remaining team is accounted for. The date is a checkpoint, not a
   hard cutoff — but a single holdout cannot block removal indefinitely.
5. **Remove + schema drop** — same as fast path steps 2–4.

### Reference: comms levers

| Lever | Mechanism | When |
|---|---|---|
| Changelog/docs | User docs + changelog entry | Every deprecation, day 0 |
| In-feature warning | Warning callout on the feature's own templates with removal date + migration link | Used tier, day 0 → removal |
| Banner | `apps/banners` Banner row, scoped location if one exists, else global, with date range | Used tier, day 0 (optional reminder for the last 2 weeks) |
| In-product notification | `apps/ocs_notifications` to affected teams | Used tier, day 0 |
| Email | One-off management command / shell script to admins of affected teams, listing their configs | Used tier, day 0 |

### Reference: read-only enforcement

Block create/edit at the **view layer** — gate the create/edit views and remove
"New"/"Edit" buttons from templates — not the model layer. Existing runtime
behaviour (e.g. a session following a configured survey link) must keep working
untouched. Delete stays allowed; it helps drain usage. API write endpoints for
the feature return `403` with a deprecation message. Where the feature's config
is set inside a larger form (e.g. `pre_survey` on the experiment form), the
field becomes disabled-with-warning rather than removed, so existing values
remain visible until removal.

### Reference: data removal and versioned models

The default data policy is a **two-phase drop**:

- **Phase 1** (code removal): models/columns untouched. `VersionField` entries
  for the feature are removed from `_get_version_details` so the version-diff
  UI stops showing it; new snapshots stop copying the field
  (`_copy_attr_to_new_version` calls removed).
- **Phase 2** (schema drop, a following release): a data migration nulls or
  deletes config rows **including on historical version rows** — accepted data
  loss; the audit log (`django-field-audit`) retains history. Then drop
  columns/tables.

Checklist items: confirm the migration is backwards-compatible (PR template
checkbox), and audit `on_delete` behaviour before dropping FKs so cascades
don't reach version rows unexpectedly. The section points at
`docs/agents/django_model_versioning.md` and
`docs/developer_guides/custom_migrations.md`.

### Reference: HTTP surfaces (public endpoints, API routes, webhooks)

- **Audit from logs**: 90-day request counts; attribute to teams via URL
  params/auth where possible.
- **During the window**: responses gain `Deprecation: true` and
  `Sunset: <http-date>` headers via a small decorator/mixin (snippet included
  in the guide). If a successor URL exists, advertise it with a
  `Link: <...>; rel="successor-version"` header and in docs.
- **At removal**: the endpoint returns **`410 Gone`** with a short HTML/JSON
  body pointing at the replacement — never a silent 404. Where the replacement
  is a true drop-in, a permanent redirect is acceptable instead. The 410 stub
  stays for at least one release cycle before the URL is deleted entirely.
- Versioned API routes (ADR-0022) deprecate per-version: a v1 endpoint's sunset
  is announced in API docs and headers; v2 is never retrofitted with the old
  behaviour.

## Out of scope

- Executing the surveys or `/embed` deprecations (separate work; each will
  instantiate a tracking issue from the new guide).
- New tooling or shared code (e.g. a reusable sunset-header decorator lives as
  a snippet in the guide until a second consumer exists).
- Deprecation policy for the public API as a whole (ADR-0022 covers API
  versioning; this guide only adds the per-endpoint sunset mechanics).
