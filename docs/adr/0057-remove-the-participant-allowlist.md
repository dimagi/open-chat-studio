# ADR-0057: Remove the participant allowlist

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Barry Tandy · Created: 2026-08-25</p>

<p class="adr-meta">Supersedes in part: <a href="0039-require-proof-of-possession-for-chat-session-access.md">ADR-0039</a>, <a href="0044-durable-per-channel-widget-auth-policy.md">ADR-0044</a></p>

## Context

`Experiment.participant_allowlist` gated three surfaces: the per-message `ParticipantValidationStage` run on every channel, the token-less fallback in `SessionAccessPermission._has_legacy_access` (described in ADR-0039 (Consequences) and ADR-0044 (Decision)), and the legacy public start page plus its Share button. The public channel (#3682) serves anonymous visitors, and the consent page's identifier capture goes with #4196. It is removed entirely, with no deprecation window for the v2 inspect API field: nothing on web can match it once identifier capture goes, and the messaging-channel use was negligible.

## Decision

We will remove the feature in two phases.

- **Phase 1 (this branch):** all reads and writes go. `ParticipantValidationStage` becomes `ParticipantIdentifierStage`; `ctx.participant_allowed` is deleted. The keyless fallback in `_has_legacy_access` is unconditional for non-widget sessions and `NONE`-level widget channels. The legacy start page and Share dialog no longer gate on it. The settings section, form field, `normalize_participant_allowlist`, the v2 inspect and write API fields, and the version-diff field go.
- **Phase 2 (#4196 sweep):** the column, its `VERSIONED_CONTENT_FIELDS` entry, the export-surface field, and `VersionFieldDisplayFormatters.format_array_field` are dropped.

## Consequences

- Token-less sessions on chatbots with a stored allowlist go from 403 to readable and writable by whoever holds the session UUID.
- Chatbots whose public link was allowlist-disabled become reachable at their existing `public_id` URL.
- Messaging-channel bots restricted by allowlist answer any sender.
- Until Phase 2, new versions keep cloning the dormant column value, and `compare_with_latest()` no longer sees allowlist changes.
- The evaluation pipeline drops its inert `PersistenceStage` explicitly, since it had relied on the deleted flag.
- Spans recorded before this release keep the old stage name.
- A v2 write request (`POST`/`PATCH` chatbot) that still sends `participant_allowlist` is rejected with `400` as an unknown key, since `ChatbotWriteSerializer` rejects unknown keys; the field is not silently ignored.
- The token-less widening is bounded: new non-widget sessions require a session token (ADR-0044), so it reaches only sessions backfilled as token-less and `NONE`-level widget channels.

## Alternatives considered

- **Keep the check on messaging channels until #4196** - rejected: it would leave a half-removed feature and a per-message check whose only remaining users were negligible.
- **Block creating a public channel while an allowlist is set** - moot, since the allowlist no longer exists.
