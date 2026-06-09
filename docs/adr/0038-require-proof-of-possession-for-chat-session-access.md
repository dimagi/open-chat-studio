# ADR-0038: Require proof of possession for chat session access

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-09</p>

## Context

The chat API (`/api/chat/<session_id>/...`) treated the session `external_id` as a bearer credential. For a public chatbot a bare request with only the session ID returned the full transcript; for widget chatbots the only extra requirement was the embed key, which ships publicly in the host page's HTML, and the `Origin`/`Referer` domain check is browser-enforced so it does not protect direct API calls. Session IDs travel through URLs, server logs, and `localStorage`, and read access never expired — a leaked ID granted permanent transcript access.

## Decision

We will treat the session ID as an identifier, not a credential. Access to a token-required session through any of the four session endpoints (`message`, `upload`, `poll`, `task-poll`) requires one of:

- a valid session token presented in the `X-Session-Token` header (mechanics in ADR-0039), or
- an authenticated user who **is** the session's participant (`participant.user_id == request.user.id`).

A `SessionAccessPermission` enforces this, replacing the prior `LegacySessionAccessPermission`. Team membership alone does **not** grant access — only the session's own participant-user bypasses the token. Denials return HTTP 403 with a machine-readable `code`: `session_token_required`, `session_token_invalid`, or `session_expired`. `WidgetDomainPermission` is unchanged and still applies to embed-key requests.

## Consequences

- A leaked session ID no longer yields transcript access; the embed key alone no longer suffices.
- Clients must carry a per-session secret (or be the authenticated participant), changing the contract for every session endpoint.
- The distinct 403 codes let the widget distinguish "restart silently" from "tell the user the conversation expired".
- The authenticated login-required web-chat page keeps working (the viewer is the participant); the anonymous public web-chat routes under the `flag_chat_widget` POC flag return 403 on poll until the bundled widget gains token support.
- Sessions that opt out (ADR-0040) keep the historical access rules: widget-auth pass-through, public-experiment access, and participant allowlist.

## Alternatives considered

- **Keep the session ID as a bearer token** — rejected: it leaks through URLs, logs, and storage, and grants permanent access.
- **Restrict the token to read endpoints only** — rejected: leaves message/upload abuse open; the token is the session credential for the whole surface.
- **Allow any team member to access any session in the team** — rejected: broader than any current flow needs; only the participant-user binding is required.
