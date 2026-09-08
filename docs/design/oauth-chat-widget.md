---
status: extracted
---

# Chat API admission model: OAuth, embed keys, and deny-by-default

Design document for [#3893](https://github.com/dimagi/open-chat-studio/issues/3893), which added an
OAuth credential to the chat API. A chatbot is exposed over the chat API by its Chat API Channel. The
channel's credential mode determines whether a caller must present the embed key or a
client-credentials token. Admission is decided when a session is created; subsequent requests are
governed by ADR-0039's proof of possession. All of the work described here has shipped, including
widget release 0.12.0.

The anonymous keyless path is not covered here. Its removal is specified in
[keyless-chat-start-sunset.md](keyless-chat-start-sunset.md).

## Decisions

Extracted 2026-09-02. The decisions from this document are recorded as:

- [ADR-0059](../adr/0059-chat-api-channel-credential-mode.md) — the Chat API Channel's credential mode determines which credential admits a caller
- [ADR-0060](../adr/0060-each-credential-validates-its-own-origin.md) — each credential validates its own origin
- [ADR-0061](../adr/0061-bearer-token-authenticator-first-on-session-start-only.md) — the bearer token is resolved by an authenticator, first in the list, on session start only
- [ADR-0062](../adr/0062-uniform-401-for-anonymous-admission-failures.md) — anonymous admission failures at session start return one uniform 401
- [ADR-0063](../adr/0063-session-start-requires-a-chat-start-machine-token.md) — session start requires a client-credentials token with the `chat:start` scope
- [ADR-0064](../adr/0064-per-channel-session-token-lifetime-override.md) — channels may override the session token lifetime

Decisions this document depended on that are recorded elsewhere:

- [ADR-0053](../adr/0053-chat-session-start-requires-membership-or-embed-key.md) — starting a session requires team membership or the embed key
- [ADR-0054](../adr/0054-chat-session-tokens-expire-on-absolute-age.md) — session tokens expire on absolute age
- [ADR-0056](../adr/0056-client-credentials-applications-name-their-chatbots.md) — client-credentials applications name the chatbots they may reach

## Open items

- Whether the two-person setup process (a Team Admin registers the application, a Chatbot Admin sets
  the channel mode) works in practice. The channel dialog lists the applications that include the
  chatbot and warns when there are none.
- Trusted participant identity for OAuth callers (asserting `participant_remote_id`).
- A per-application scope allowlist, which would close the gap recorded in ADR-0063.
- The bound widget has no way to start a new session when the session lifetime expires (ADR-0054). The
  bound widget and the `oauth` restart (ADR-0064) share the `session_expired` handling, so a
  fix must be tested against both.
- Whether the inspect API should report `credential_mode`. It does not today.
