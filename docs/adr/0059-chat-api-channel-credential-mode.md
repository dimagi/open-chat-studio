# ADR-0059: The Chat API Channel's credential mode determines which credential admits a caller

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0044-durable-per-channel-widget-auth-policy.md">ADR-0044</a>, <a href="0053-chat-session-start-requires-membership-or-embed-key.md">ADR-0053</a></p>

## Context

After ADR-0053, an anonymous caller can start a chat session with the chatbot's embed key and a logged-in caller can start one through team membership. Issue #3893 requested a third option: a host wants its embedded widget, or a server integration, to reach a chatbot only when it presents an OAuth token, so that the embed key alone is not sufficient to use the chatbot from another site.

This requires a per-chatbot setting that records the admin's choice. The channel already has `required_auth_level` (ADR-0044), but that field records the capability of the deployed widget and is raised automatically by the ADR-0045 ratchet. An admin's access policy must not change as a side effect of a widget upgrade, so it cannot be stored in that field.

The widget serves two platforms: `EMBEDDED_WIDGET`, and `PUBLIC`, an OCS-hosted link enabled by `flag_public_channel`.

## Decision

We will record the admin's choice in `ExperimentChannel.credential_mode`, a `CredentialMode` column on the Chat API Channel (platform `embedded_widget`).

- Two values: `embed_key` (the default; every existing channel was migrated to it) and `oauth`. The column is audited.
- The mode applies to `EMBEDDED_WIDGET` channels only. A `PUBLIC` channel always admits by embed key.
- In `oauth` mode the embed key is ignored, not rejected. A snippet that sends `X-Embed-Key` continues to work if it also presents a valid token. The key alone does not admit a caller.
- The mode determines which external credential admits an anonymous caller: the embed key in `embed_key` mode, a bearer token in `oauth` mode. It does not affect team membership: ADR-0053's membership path is unchanged in both modes.
- The mode applies to `/api/chat/*` only. The `chatbots:interact` endpoints do not resolve a channel and are bounded by the ADR-0056 allowlist alone.
- When the channel form omits `credential_mode`, the stored mode is kept. A partial save cannot change the required credential to a weaker one.
- `oauth` mode sets `required_auth_level` to `SESSION_TOKEN`. The check constraint `oauth_credential_mode_requires_session_token` rejects any other combination, and the ratchet skips these channels.
- The platform value `embedded_widget` is unchanged; its label becomes "Chat Widget & API". The value is also used as `Participant.platform`, so renaming it would create a second participant record for every existing participant.
- In `oauth` mode the channel reports the first widget release that supports the `authTokenProvider` property as its minimum widget version. The minimum is advisory: the form warns when a browser-facing channel's last-reported widget version is lower, but no request is rejected because of it.

There is no third mode requiring both key and token. In a browser embed the key is in page source and the token is available to page JavaScript, so an attacker who can obtain the token from the page can also obtain the key, and requiring both adds no protection against that attacker. The per-chatbot restriction that such a mode would provide is provided instead by the application allowlist (ADR-0056; applied to `chat:start` in ADR-0063).

## Consequences

- Existing channels are unaffected; `embed_key` mode is the previous behaviour.
- An `oauth` channel with `required_auth_level` below `SESSION_TOKEN` would issue no session token, and every subsequent call would fail the legacy-access check. The constraint prevents this state.
- All modes produce `platform=embedded_widget` participants.
- A chatbot has one Chat API Channel, so a single embed cannot be both anonymous and token-gated. A `PUBLIC` channel on the same chatbot provides an anonymous OCS-hosted link alongside a token-gated embed.
- One channel per platform is enforced only by the add-channel dropdown, not by a database constraint. Allowing two Chat API Channels per chatbot would require no migration.
- Switching an existing channel to `oauth` requires no snippet change other than installing an `authTokenProvider` function on the element.

## Alternatives considered

- **A new platform for OAuth callers** → rejected: it creates a second `Participant.platform` value for every existing participant and duplicates the widget-specific code paths.
- **`require_oauth` in `extra_data`** → rejected: ADR-0044 stored per-channel policy in a real column, and this field is the same kind of setting.
- **A boolean on `Experiment`** → rejected: the chatbot is versioned, and the session needs a channel to be attributed to.
- **A third `WidgetAuthLevel` value** → rejected: that field is version-ratcheted (see Context).
- **Embed key and token together** → rejected, see Decision.
