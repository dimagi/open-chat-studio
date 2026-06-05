---
status: active
---

# Per-session token for the chat API

## Problem

The widget/chat API (`/api/chat/<session_id>/...`) treats the session
`external_id` as a bearer credential. For a public chatbot, a bare request with
only the session ID returns the full transcript; for widget chatbots the only
extra requirement is the embed key, which is public in the host page's HTML
(and the `Origin`/`Referer` domain check is browser-enforced, so it does not
protect direct API access). Session IDs travel through URLs, server logs, and
`localStorage`, and read access never expires — a leaked ID grants permanent
transcript access.

## Goal

Possession of a session ID alone must not grant access to a session. Access
requires proof of possession of a secret issued when the session is created
(or an authenticated user with rights to the session).

## Decisions

| Decision | Choice |
|---|---|
| Token type | Stateless signed token (`django.core.signing`, HMAC over `SECRET_KEY`) |
| Scope | All session endpoints: `message`, `upload`, `poll`, `task-poll` |
| Enforcement marker | New model field `ExperimentSession.session_token_required` |
| Default | `True` (fail closed); **opt-out**, not opt-in |
| Existing sessions | Backfilled to `True` if inactive >24h at migration time, `False` if recently active |
| Old widgets | Implicit opt-out at session start: `x-ocs-widget-version` header present but no `use_session_token` field |
| Expiry | Generous global inactivity backstop (default 7 days), not encoded in the token |
| Authenticated users | Bypass the token via Django session auth + session-access check |

## Token mechanics

- Token = `signing.dumps({"sid": str(session.external_id)}, salt="ocs.chat.session-token")`.
  Carries the session ID and issuance timestamp; nothing stored in the DB.
- Issued by `chat_start_session` in the response (`session_token`) whenever the
  session is created with `session_token_required=True`.
- Trusted server-side contexts can re-derive a token for any session at any
  time (a deliberate property of the stateless design — e.g. for bound-session
  pages rendered by Django views, or future admin tooling).
- Expiry is **not** in the token. A token-required session rejects all token
  access once the session has been inactive for longer than the window.
  Activity = latest `ChatMessage.created_at` (falling back to
  `session.created_at`). Polling does not count as activity, so a leaked token
  cannot keep itself alive. Window: new setting
  `CHAT_SESSION_TOKEN_INACTIVITY_WINDOW` (timedelta, default 7 days). This is
  a generous backstop, not a tight match for the widget's persistence: the
  widget's `persistentSessionExpire` is configured by the embedding site and
  unknowable server-side, so the server window just has to comfortably exceed
  any reasonable widget config while still ending "permanent" access.
- `SECRET_KEY` rotation is handled by Django: `signing.loads` verifies
  against `SECRET_KEY_FALLBACKS`, so a proper rotation keeps live tokens
  valid. An abrupt rotation without fallbacks invalidates tokens; the
  widget's 403-recovery path (below) degrades that to "a new conversation
  starts" rather than a hard failure.

## Server-side changes

### Model + migration

`ExperimentSession.session_token_required = models.BooleanField(default=True)`

- Schema default `True` so every new session, from **every** creation path
  (chat API, server-rendered web chat, channel platforms), is fail-closed.
  This matters because `chat_poll_response` will return messages for *any*
  session looked up by `external_id` (e.g. Telegram sessions of public bots).
- The migration backfills existing rows by activity: sessions whose latest
  message is **older than 24 hours** at migration time get `True` (their
  transcripts are immediately protected from anonymous bearer-ID reads;
  authenticated users retain access via the bypass), while recently-active
  sessions get `False` so live conversations are not interrupted.
- Known edge: the backfill ships in the server PR, before the token-aware
  widget exists. An old widget on a site with a `persistent-session-expire`
  longer than 24h could resume a stale (now token-required) session and hit
  403s with no recovery logic. Default-config widgets discard sessions after
  24h themselves, so the blast radius is sites with custom long persistence
  resuming >24h-stale sessions. Accepted.

### Session-start flag logic (`chat_start_session`)

New optional request field `use_session_token` (boolean) on
`ChatStartSessionRequest`:

- `false` → session created with `session_token_required=False`, no token
  returned (explicit opt-out for API consumers that can't adopt tokens yet).
- `true` → enforced; `session_token` in the response.
- **absent** → enforced, **unless** the request carries an
  `x-ocs-widget-version` header, in which case it is an old widget (every
  token-aware widget release always sends the field) and is treated as an
  implicit opt-out. Old embedded widgets keep working without changes; no
  hardcoded version threshold is needed. Spoofing the header only lets a
  client weaken *its own new* session (today's baseline); it cannot downgrade
  an existing session. The sniff is temporary and can be removed once old
  widget usage dies off.

This is a documented breaking change for direct API consumers who create
sessions via `/api/chat/start/` and ignore the response token: their fix is to
send the returned `session_token` (or explicitly opt out). Needs changelog and
API-docs updates.

### Permission enforcement

Replace `LegacySessionAccessPermission` with `SessionAccessPermission`,
applied to all four session endpoints:

1. Look up the session (existing cached helper). Missing → deny.
2. `session_token_required=False` → exactly today's legacy behavior
   (widget-auth pass-through, `is_public`, participant allowlist).
3. `session_token_required=True`:
   - Django-session-authenticated user who is the session's participant
     (`participant.user_id == request.user.id`) or a member of the session's
     team → allow (this keeps the OCS-hosted `web_chat.html` page working
     unchanged: same-origin fetch sends the session cookie; CSRF is already
     handled by the widget). For the *anonymous* public web-chat routes
     (`chatbot_chat`, `chatbot_chat_embed`, behind `flag_chat_widget`), the
     server opts the session out of token enforcement at render time until the
     bundled widget supports session tokens.
   - Otherwise require `X-Session-Token`: signature valid (salt + secret),
     `sid` equals the path `session_id`, inactivity window not exceeded.
     **The embed key alone no longer suffices for these sessions.**
4. Failures return 403 with machine-readable `code`:
   `session_token_required`, `session_token_invalid`, or `session_expired`.

`WidgetDomainPermission` is unchanged and still applies (browser-level
protection for embed-key requests).

## Widget changes (`components/chat_widget`)

- Session start sends `use_session_token: true`; the returned token is kept
  with the session and sent as `X-Session-Token` on every session call
  (message, upload, poll, task-poll).
- `persistentSession` mode stores the token in `localStorage` alongside the
  existing `sessionId` / `lastActivity` / `messages` keys.
- Bound-session mode gets a new `session-token` prop alongside `session-id`,
  for host pages whose backend created the session via the API.
- On a 403 with a token error code: clear the persisted session and start a
  new one. For `session_expired`, show a brief "conversation expired" notice
  first.
## Delivery

Per the documentation and changelog policy, the widget changes ship in a
separate PR from the server changes:

1. **PR 1 — server side**: model field + backfill migration, permission
   class, start-endpoint flag logic, setting, API docs + changelog. On its
   own this is fully backward compatible for widgets (every current widget
   sends the version header and so implicitly opts out); it is the breaking
   point for header-less API consumers, documented in the changelog.
2. **PR 2 — widget** (`components/chat_widget`): token handling, persistence,
   `session-token` prop, 403 recovery; its own docs/changelog. Released to
   npm.
3. **PR 3 — version bump**: the Django app bumps its pinned widget version
   (widget changes do not reach the app until released and bumped).

## Out of scope / future hardening

- Rate limiting on the chat endpoints (separate piece of work).
- Removing the widget-version sniff once pre-token widgets age out.
- Revoking individual sessions (stateless tokens are not individually
  revocable; flipping `session_token_required` off/on or ending the session
  are the levers available).

## Testing

- **API tests**, per endpoint, across the matrix:
  - legacy session (`session_token_required=False`): behavior unchanged;
  - token session × {valid token, missing, bad signature, `sid` mismatch,
    inactivity-expired};
  - authenticated bypass: participant user, team member, unrelated
    authenticated user (denied);
  - widget-auth (embed key) request against a token-required session without
    a token: denied;
  - start endpoint flag logic: explicit true/false, absent with widget
    version header (implicit opt-out), absent without header (enforced);
    token present in response only when enforced.
- **Migration test**: backfill sets `True` for sessions inactive >24h and
  `False` for recently-active ones; new rows default `True`.
- **Widget jest tests**: opt-in sent on start; header included on all calls;
  localStorage round-trip including token; bound-mode `session-token` prop;
  403 token-error recovery (clear + restart, expired notice).
