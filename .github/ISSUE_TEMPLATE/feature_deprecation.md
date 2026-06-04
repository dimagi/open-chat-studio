---
name: Feature deprecation
about: Track the deprecation and removal of a feature. See docs/developer_guides/feature_deprecation.md
title: "Deprecate: <feature name>"
labels: maintenance
---

<!--
This issue is the single source of truth for this feature's audit results,
dates, and stage progress. Process reference:
https://github.com/dimagi/open-chat-studio/blob/main/docs/developer_guides/feature_deprecation.md

After running the Stage 0 audit, delete whichever tier section below does not apply.
-->

**Feature:** <name>
**Surfaces affected:** <UI pages / models & fields / endpoints / docs pages>
**Replacement:** <successor feature, or "none">
**Usage audit:** <date run> — <result summary, link to script + output>
**Tier:** fast path (unused) | full lifecycle (used)
**Announced removal date:** <date, full lifecycle only>

## Stage 0: usage audit

- [ ] Audit script run over a 90-day lookback window; script + output attached to this issue
- [ ] Tier chosen: zero active usage → fast path; any active usage → full lifecycle

## Fast path (unused)

- [ ] Stage 0 audit attached to this issue; zero active usage in 90 days
- [ ] Announce: changelog entry + deprecation note in user docs naming the
      removal release and how to object (support channel / GitHub issue)
- [ ] Remove code (next release): UI entry points, views, URLs, forms,
      business logic. Models/columns and dormant config rows stay.
- [ ] Schema drop (a following release): data migration cleans up dormant
      config rows, then drops columns/tables.
- [ ] Close out: remove user docs pages (or leave a tombstone note pointing
      at the replacement); close this issue.

## Full lifecycle (used)

- [ ] Stage 0 audit attached to this issue; affected teams listed
- [ ] Day 0 — announce on all channels (see Comms levers in the guide):
    - [ ] changelog entry + user docs deprecation note
    - [ ] banner (scoped location if one exists, else global) with date range
    - [ ] in-product notification to affected teams
    - [ ] email to admins of affected teams listing *their* configs and the
          removal date
    - [ ] in-feature warning on the feature's own pages with removal date and
          migration path
- [ ] Same release — read-only mode (see Read-only enforcement in the guide)
- [ ] Deprecation window (60 days): support migration; re-run the audit
      periodically and note progress here
- [ ] Removal checkpoint (date passed): re-run the audit. Triage each
      remaining team: contacted/migrated, or breakage explicitly accepted by
      the feature owner. Record the outcome here. Do not proceed until every
      remaining team is accounted for.
- [ ] Remove code: UI entry points, views, URLs, forms, business logic.
      Models/columns stay.
- [ ] Schema drop (a following release): data migration, then drop
      columns/tables.
- [ ] Close out: remove user docs pages (or leave a tombstone note); close
      this issue.
