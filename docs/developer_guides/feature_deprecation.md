# Feature Deprecation and Removal

This guide describes how to retire a feature from Open Chat Studio: measuring
usage, communicating with affected teams, gating access during the wind-down,
and removing code and data safely.

Every deprecation is tracked as a **GitHub issue** on `dimagi/open-chat-studio`.
Create it with the **Feature deprecation** issue template
(`.github/ISSUE_TEMPLATE/feature_deprecation.md`), which contains the
front-matter block and both checklists — delete the tier that doesn't apply
after the Stage 0 audit. The issue is the single source of truth for the
feature's audit results, dates, and stage progress.

## Tracking issue front matter

The front matter at the top of the tracking issue:

```markdown
**Feature:** <name>
**Surfaces affected:** <UI pages / models & fields / endpoints / docs pages>
**Replacement:** <successor feature, or "none">
**Usage audit:** <date run> — <result summary, link to script + output>
**Tier:** fast path (unused) | full lifecycle (used)
**Announced removal date:** <date, full lifecycle only>
```

## Stage 0: usage audit (always)

Every deprecation starts with a usage audit over a **90-day lookback window**,
recorded on the tracking issue.

**In-app features** — write a throwaway Django shell script measuring, per team:

1. **Configured**: teams/objects that have the feature set up.
2. **Active**: usage events within the window (sessions, messages, task runs —
   whatever "the feature did something" means for this feature).

Skeleton:

```python
# Throwaway report. Run with: uv run python manage.py shell < <feature>_usage.py
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

cutoff = timezone.now() - timedelta(days=90)

# 1. configured: rows with the feature set up, grouped by team
# 2. active: annotate a Count(...) filtered on activity since `cutoff`
# Write results to CSV and attach to the tracking issue.
```

Attach the script and its output to the tracking issue (paste or gist) — do
not commit it to the repo.

**HTTP surfaces** (public endpoints, API routes, webhooks) — pull request
counts per endpoint from the server logs/metrics over the same 90 days, and
attribute requests to teams via URL parameters or authentication where
possible.

### Choosing the tier

A feature is **unused** if there is **zero active usage** in the window.
Configured-but-dormant counts as unused: dormant config is data to clean up,
not usage to migrate. Anything else is **used**.

- Unused → [fast path](#fast-path-unused-features)
- Used → [full lifecycle](#full-lifecycle-used-features)

## Fast path (unused features)

There is no grace period beyond the natural gap between releases.

```markdown
- [ ] Stage 0 audit attached to this issue; zero active usage in 90 days
- [ ] Announce: changelog entry + deprecation note in user docs naming the
      removal release and how to object (support channel / GitHub issue)
- [ ] Remove code (next release): UI entry points, views, URLs, forms,
      business logic. Models/columns and dormant config rows stay.
- [ ] Schema drop (a following release): data migration cleans up dormant
      config rows, then drops columns/tables. Migration is
      backwards-compatible (PR template checkbox).
- [ ] Close out: remove user docs pages (or leave a tombstone note pointing
      at the replacement); close this issue.
```

See [Data removal and versioned models](#data-removal-and-versioned-models)
before writing the removal PRs.

## Full lifecycle (used features)

The removal date is **announcement + 60 days** minimum.

```markdown
- [ ] Stage 0 audit attached to this issue; affected teams listed
- [ ] Day 0 — announce on all channels (see Comms levers):
    - [ ] changelog entry + user docs deprecation note
    - [ ] banner (scoped location if one exists, else global) with date range
    - [ ] in-product notification to affected teams
    - [ ] email to admins of affected teams listing *their* configs and the
          removal date
    - [ ] in-feature warning on the feature's own pages with removal date and
          migration path
- [ ] Same release — read-only mode (see Read-only enforcement)
- [ ] Deprecation window (60 days): support migration; re-run the audit
      periodically and note progress here
- [ ] Removal checkpoint (date passed): re-run the audit. Triage each
      remaining team: contacted/migrated, or breakage explicitly accepted by
      the feature owner. Record the outcome here. Do not proceed until every
      remaining team is accounted for.
- [ ] Remove code: UI entry points, views, URLs, forms, business logic.
      Models/columns stay.
- [ ] Schema drop (a following release): data migration, then drop
      columns/tables. Migration is backwards-compatible.
- [ ] Close out: remove user docs pages (or leave a tombstone note); close
      this issue.
```

The removal date is a **checkpoint, not a hard cutoff** — removal requires
both the date passing *and* remaining usage being triaged — but a single
holdout cannot block removal indefinitely.

## Comms levers

| Lever | Mechanism | When |
|---|---|---|
| Changelog/docs | Entry in the [docs repo](https://github.com/dimagi/open-chat-studio-docs) — see [User Docs](user_docs.md) | Every deprecation, day 0 |
| In-feature warning | Warning callout on the feature's own templates with removal date + migration link | Used tier, day 0 → removal |
| Banner | `apps/banners` `Banner` row; scoped location if one exists, else global; set `start_date`/`end_date` | Used tier, day 0 (optionally a second reminder banner for the final 2 weeks) |
| In-product notification | `apps/ocs_notifications` notification to affected teams — see [Notifications](notifications.md) | Used tier, day 0 |
| Email | One-off shell script / management command emailing admins of affected teams, listing their configs | Used tier, day 0 |

## Read-only enforcement

During the deprecation window, block **create and edit at the view layer** for
all teams; do not touch the model layer:

- Gate the create/edit views (return a redirect or 403 with a deprecation
  message) and remove "New"/"Edit" buttons from templates.
- Existing runtime behaviour must keep working untouched — e.g. a session
  following an already-configured survey link still works.
- **Delete stays allowed** — it helps drain usage.
- API write endpoints for the feature return `403` with a deprecation message
  in the body.
- Where the feature's config is a field inside a larger form (e.g.
  `pre_survey` on the experiment form), disable the field and show the warning
  rather than removing it, so existing values stay visible until removal.

## Data removal and versioned models

The default data policy is a **two-phase drop**:

**Phase 1 — code removal.** Models and columns are untouched; the feature is
recoverable by reverting the PR. For versioned models (see
[Object Versioning](versioning.md) and `docs/agents/django_model_versioning.md`):

- Remove the feature's `VersionField` entries from `_get_version_details` so
  the version-diff UI stops showing it.
- Remove the feature's `_copy_attr_to_new_version` calls so new snapshots stop
  copying the field.

**Phase 2 — schema drop**, in a later release:

- A data migration nulls or deletes config rows **including on historical
  version rows**. This is accepted data loss: the audit log
  (`django-field-audit`) retains the history.
- Then drop the columns/tables.
- Confirm the migration is backwards-compatible (PR template checkbox) and
  audit `on_delete` behaviour before dropping FKs so cascades don't reach
  version rows unexpectedly. See [Data Migrations](custom_migrations.md).

## HTTP surfaces

Deprecating a public endpoint, API route, or webhook needs different tools —
its users are external callers who never see in-app comms.

**Audit** — 90-day request counts from logs/metrics, attributed to teams where
possible (URL params, auth).

**During the window** — responses gain deprecation headers:

```python
import functools
from datetime import datetime

from django.utils.http import http_date


def sunset(sunset_at: datetime, successor_url: str | None = None):
    """Mark a view as deprecated with RFC 8594 Sunset / Deprecation headers."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = http_date(sunset_at.timestamp())
            if successor_url:
                response.headers["Link"] = f'<{successor_url}>; rel="successor-version"'
            return response

        return wrapper

    return decorator
```

This lives as a snippet here until a second consumer exists — copy it next to
the view being deprecated. Note: on public views, `@waf_allow` must remain the
first decorator (enforced by a pre-commit hook).

If a successor URL exists, advertise it in the `Link` header (above) and in
the endpoint's docs.

**At removal** — the URL returns **`410 Gone`** with a short body pointing at
the replacement; never a silent 404:

```python
from django.http import HttpResponseGone


def old_endpoint_removed(request, *args, **kwargs):
    return HttpResponseGone(
        "This endpoint was removed on <date>. Use <replacement> instead: <docs link>"
    )
```

Where the replacement is a true drop-in, a permanent redirect
(`HttpResponsePermanentRedirect`) is acceptable instead. The `410` stub stays
for **at least one release cycle** before the URL is deleted entirely.

**Versioned API routes** (see
[ADR-0022](../adr/0022-url-path-api-versioning.md)) deprecate per-version: announce the v1
endpoint's sunset in the API docs and via the headers above; never retrofit
the old behaviour into v2.
