# ADR-0044: Durable per-channel widget auth policy

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-07-21</p>

<p class="adr-meta">Extends: <a href="0041-fail-closed-session-token-enforcement-rollout.md">ADR-0041</a></p>

## Context

ADR-0041 decides *whether* a new session enforces a token from two per-request signals on the start endpoint: the `use_session_token` body field and an `x-ocs-widget-version` header sniff. It called that sniff temporary and removable once pre-token widgets age out. The signals are re-evaluated on every start and carry no memory: a channel running an old widget re-proves its age each request, and an operator has no durable record of what a given embedded widget is trusted to send. The start endpoint (`chat_start_session`) authenticates only widgets (embed key) and OCS users (Django session) — there is no API-key surface, so there are no genuine "direct API consumers" for the field to serve.

## Decision

We will make the enforcement decision a durable per-channel policy. `ExperimentChannel.required_auth_level` (`WidgetAuthLevel`: NONE / EMBED_KEY / SESSION_TOKEN) holds the minimum authentication an embedded widget must present; it defaults to SESSION_TOKEN and is only meaningful for EMBEDDED_WIDGET channels. Session start issues a token when the channel's level is SESSION_TOKEN or the channel is non-widget (`widget_auth_level` is `None`, e.g. the team API channel), and opts out otherwise. `SessionAccessPermission` gates the legacy path on the level: it blocks the public/allowlist fallback for any widget channel at EMBED_KEY or above, requires the embed key to authenticate the session's *own* channel (no cross-channel access), and never lets a valid embed key alone satisfy a SESSION_TOKEN channel even on a session left `session_token_required=False`. Existing channels are grandfathered by migration from the widget version they last reported.

This replaces ADR-0041's opt-out mechanism: the `use_session_token` field and the version sniff are removed. ADR-0041's core — `session_token_required` defaulting to `True` — stands unchanged.

The field is set by migration and model default only; it is not exposed in the channel edit form.

## Consequences

- The trust decision is inspectable and stable per channel, not re-derived per request.
- **Breaking change**: the `use_session_token` request field is gone from the start endpoint and its schema; clients that sent it are silently ignored (unknown fields are dropped) and get the channel's policy instead.
- The version-sniff heuristic and its tests are retired; behaviour is now covered by explicit per-level tests.
- A non-widget channel always issues a token (fail closed); authenticated users still reach their own sessions via the participant-user bypass.

## Alternatives considered

- **Keep `use_session_token` as an override** — rejected: no supported caller needs a per-request override, and a mutable request signal is weaker than a durable channel column.
- **Expose `required_auth_level` in the channel form** — rejected: operators shouldn't hand-downgrade auth; the migration sets the correct floor and new channels default to the strictest level.
- **Infer the level per request from the widget version** — rejected: that is the transient sniff ADR-0041 already earmarked for removal.
