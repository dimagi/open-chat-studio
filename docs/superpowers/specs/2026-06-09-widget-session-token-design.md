---
status: active
extends: ../../adr/0039-require-proof-of-possession-for-chat-session-access.md
---

# Chat widget session-token support

> Widget-side counterpart to the server work in
> [PR #3552](https://github.com/dimagi/open-chat-studio/pull/3552)
> (ADR-0039 / ADR-0040 / ADR-0041). This is the second of the three rollout PRs
> described in those ADRs.

## Problem

PR #3552 makes chat sessions fail closed: every new session defaults to
`session_token_required=True`. Token-protected sessions demand a valid
`X-Session-Token` header (or an authenticated participant) on the four protected
endpoints:

- `POST /api/chat/{session_id}/message/`
- `GET  /api/chat/{session_id}/{task_id}/poll/`
- `GET  /api/chat/{session_id}/poll/`
- `POST /api/chat/{session_id}/upload/`

The chat widget (`components/chat_widget`) does not yet request, store, or send a
token, so a token-protected session would be rejected. This spec covers the
widget changes — and the server-rendered web-chat page wiring — needed to make the
widget a first-class token-aware client.

## Server contract (from PR #3552)

- `POST /api/chat/start/` accepts `use_session_token` (bool, optional). When the
  widget sends `true`, the response includes `session_token` (a stateless signed
  token). That token must be sent as `X-Session-Token` on every subsequent
  request for the session.
- Token-bound, server-rendered pages can obtain a token without calling the start
  endpoint: `issue_session_token(session)` is stateless and mints a token for any
  session server-side.
- Rejections are `HTTP 403` with a JSON body `{"error": ..., "code": ...}` where
  `code` is one of `session_token_required`, `session_token_invalid`,
  `session_expired`.

## Decisions

These were settled during design:

1. **Always request a token.** The widget sends `use_session_token: true` on every
   start. No public opt-out prop — the widget is a token-aware client.
2. **Include the bound-session `session-token` prop** so host pages (the OCS
   web-chat page) can pass a server-minted token.
3. **403 recovery for unbound sessions: notice + auto-start fresh.** Show a brief
   system message, discard the stale session/token, and start a new session on the
   next send. Do *not* auto-resend (avoids 403 loops).
4. **Web-chat template wiring lands in this PR.** It is inert until activation
   (see below), so it is safe to include.
5. **Local widget consumption is out of scope** — the Django app keeps consuming
   the widget from the pinned npm package.

## Architecture

### Token as a single source of truth on the component

The component (`ocs-chat.tsx`) owns the current token in a private field
`currentSessionToken?: string` and pushes it into the service via
`setSessionToken`. There are two ways it is populated:

- **Unbound (self-started) session:** `startSession()` sends
  `use_session_token: true`, reads `session_token` from the response, stores it,
  and persists it to `localStorage` (see below) so a resumed session can keep
  using it.
- **Bound session (`session-id` prop):** the new `@Prop() sessionToken?`
  (attribute `session-token`) supplies a token minted by the host page. It is
  used as-is and **never persisted** — the host owns the session lifecycle.

### Service layer (`chat-session-service.ts`)

- Add `sessionToken?` to `ChatSessionServiceOptions` and a
  `setSessionToken(token?: string)` method. Include `X-Session-Token` in
  `getCommonHeaders()` when a token is set. This covers `sendMessage`,
  `pollTaskOnce`, and `fetchMessages` automatically.
- Add `session_token?: string | null` to `ChatStartSessionResponse`.
- Export a `SessionAccessError extends Error` carrying `status: number` and
  `code?: string`. `startSession`, `sendMessage`, `pollTaskOnce`, and
  `fetchMessages` throw it when the response status is `403`, parsing
  `{error, code}` from the body (falling back to `statusText` when the body is
  not JSON).

### File upload (`file-attachment-manager.ts`)

The upload endpoint is separate from `ChatSessionService`. Add `sessionToken?` to
`UploadContext` and set the `X-Session-Token` header in `uploadPendingFiles`. Add
a `tokenRejected?: boolean` flag to `UploadResult`, set when the upload returns
`403`, so the component can route an upload rejection into the same recovery path.

### Token persistence (unbound sessions)

`getStorageKeys()` gains a `sessionToken: ocs-chat-token-<chatbotId>` key. The
token is written in `saveSessionToStorage()` alongside the session id, read back
in `loadSessionFromStorage()` / `componentWillLoad`, and removed in
`clearSessionStorage()`. Bound sessions never persist (guarded by
`isSessionBound()`, same as today).

### 403 recovery

A single helper, `handleSessionAccessError()`:

- **Unbound:** add a system notice (e.g. "Your chat session expired — starting a
  new chat."), then `clearSession()` (which already discards the stored session,
  messages, and — now — the token). The next user send starts a fresh session.
- **Bound:** surface an error message only. A host-owned session cannot be
  restarted by the widget.

**Where it is called:** foreground paths that can hit a token rejection —
`sendMessage`, the task-polling `onError` callback, `loadBoundSessionHistory`,
and the upload path (via `tokenRejected`) — detect a `SessionAccessError`
(or the upload flag) and call the helper.

**Background message polling stays silent on 403:** it already swallows errors;
it simply stops. Recovery is driven by the next foreground action, so a
background poll never yanks the UI out from under the user.

### Web-chat template wiring (this PR)

- `apps/chatbots/views.py::_chatbot_chat_ui` imports `issue_session_token` from
  `apps.api.session_tokens` and adds
  `"session_token": issue_session_token(request.experiment_session)` to the
  template context.
- `templates/chatbots/chat/web_chat.html` adds
  `session-token="{{ session_token }}"` to the `<open-chat-studio-widget>` tag.

This replaces the *planned* (never-implemented) render-time opt-out with the real
fix: the page is token-protected and the widget is handed a valid token, rather
than the session being downgraded to legacy access.

## Activation (out of scope — follow-up)

The Django app loads the widget from the **pinned npm package**, not the local
source. None of these changes take effect on deployed pages — and the
`session-token` template attribute is a pure no-op on the current widget — until:

1. The token-aware widget is published to npm, and
2. The root `package.json` dependency is bumped to that version.

Deployment ordering with PR #3552 matters at that activation step, not for
merging this work.

## Testing

- **`chat-session-service.spec.ts`:** `X-Session-Token` is injected on
  message/poll/task-poll once a token is set; `session_token` is captured from the
  start response; `SessionAccessError` (with `code`) is thrown on `403`.
- **`ocs-chat_session_handling.spec.tsx`:** token persisted to and restored from
  `localStorage` for unbound sessions; unbound `403` → system notice + fresh
  start; bound `403` → error only; the `session-token` prop is used for bound
  sessions and never persisted.
- **File-attachment tests:** `X-Session-Token` is sent on upload; a `403` sets
  `tokenRejected`.

## Out of scope

- npm release + root dependency bump (activation; the rollout's PR 3).
- Switching the Django app to local widget consumption.
- The pre-existing `chat_poll_task_response` `task_id`-binding gap noted in the
  PR #3552 review ([#3577](https://github.com/dimagi/open-chat-studio/issues/3577)).
