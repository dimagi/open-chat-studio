# ADR-0038: Dataset message-clone scope is a single per-submission choice

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-06</p>

## Context

When adding sessions to a message-mode evaluation dataset, the user can clone either every message in each selected session or only the messages matching the active filters. An earlier version of the "Add Sessions" page exposed this both ways at once: a top-level "Messages to clone" radio and a per-row "Filtered" checkbox column in the sessions table. The per-row column was dead UI — its handlers were never loaded on the page, so clicking it changed nothing — and the server already read only the single top-level value. The two controls were redundant and ambiguous about which one won.

## Decision

We will treat the all-messages-vs-filtered-messages choice as a single value submitted per request, applied uniformly to every selected session, with no per-session override. The form carries one `message_scope` field (`all` or `filtered`, defaulting to `all`). The server partitions all selected sessions by that one value: `filtered` clones only filter-matching messages from every session, `all` clones whole sessions. The per-row "Filtered" column is removed from the selection table. The control is shown only when it can change the outcome — message-mode datasets with at least one active filter — and resets to `all` when the last filter is cleared, so no hidden stale value is ever submitted.

## Consequences

- The POST contract stays minimal: `mode`, `session_ids`, `sample_percent`, and a single `message_scope`. Session-mode datasets ignore `message_scope` entirely and always clone whole sessions.
- A future requirement to scope messages differently per session would need a new contract — the current one cannot express it.
- Removing the dead per-row column eliminates a control that silently did nothing, and the sessions table keeps one checkbox column for session selection only.
- The "meaningful only" visibility rule means the control's absence is itself information: if it is hidden, scope is necessarily `all`.

## Alternatives considered

- **Per-session clone scope (make the per-row column functional)** → rejected: it multiplies a one-line decision into N independent ones for no observed user need, and would require a per-session payload the Celery clone tasks do not consume.
- **Keep both the global radio and the per-row column** → rejected: two controls for one decision, with no defined precedence, was the confusion being removed.
- **Always show the global control** → rejected: for session-mode datasets and unfiltered message-mode datasets the choice has no effect, so showing it invites a selection that does nothing.
