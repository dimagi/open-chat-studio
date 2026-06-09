# ADR-0038: Redirect version snapshot URLs to the working version

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Percy · Created: 2026-06-09</p>

## Context

Every `Experiment` row is either a **working version** (family head, `working_version_id IS NULL`) or a **version snapshot** (a published copy, `working_version_id IS NOT NULL`). The `single_chatbot_home` view is designed to operate on the working version; it calls `resolve_published_or_working()` which requires a family-head experiment.

`Experiment.get_absolute_url()` historically used `self.id` unconditionally. Notification functions (trace errors, audio failures, delivery failures, etc.) receive whatever experiment object the channel was initialised with — often a published version snapshot returned by `resolve_published_or_working()`. The resulting notification link therefore contained the snapshot's PK. Visiting that URL caused `resolve_published_or_working()` to raise `ValueError`, producing an error page.

The immediate fix (PR #3569) filtered `single_chatbot_home`'s queryset to `working_version__isnull=True`, converting the error to a clean 404. This PR (#3572) replaces that with a redirect.

## Decision

We will redirect any request for `single_chatbot_home` that resolves to a version snapshot to the canonical working-version URL, appending `?version_id=<N>#versions` so the browser lands on the Versions tab with the relevant snapshot highlighted.

`Experiment.get_absolute_url()` will produce this same redirect-ready URL when called on a snapshot, so all existing and future notification links carry the version context automatically without any changes to notification call sites.

## Consequences

- Users who click a stale or snapshot-based notification link land on a useful page rather than hitting an error or a dead end.
- The `?version_id=<N>#versions` suffix preserves the context of *which* version caused the event; the Versions tab auto-selects and scrolls to that row.
- Version snapshot IDs remain permanently valid entry points — no link ever goes stale as long as the working version exists.
- `single_chatbot_home` stays simpler: it always operates on a working version, no downstream guard needed.
- Callers of `get_absolute_url()` on a snapshot receive a URL with a query string and fragment; any code that compares or strips the URL should be aware of this.

## Alternatives considered

- **Return 404 for snapshot IDs (PR #3569 approach):** Clean, but gives the user no path forward and breaks bookmarked or emailed notification links permanently.
- **Fix notification call sites to always pass the working version:** Reduces the information in the link (no `?version_id`) and requires ongoing discipline at every new notification call site. Rejected in favour of fixing `get_absolute_url()` once.
- **Add a dedicated version-detail URL:** More work, and the versions tab on `single_chatbot_home` already serves this purpose once the right tab is selected.
