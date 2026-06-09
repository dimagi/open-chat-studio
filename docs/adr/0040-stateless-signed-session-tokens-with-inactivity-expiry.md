# ADR-0040: Stateless signed session tokens with server-side inactivity expiry

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-09</p>

<p class="adr-meta">Extends: <a href="0039-require-proof-of-possession-for-chat-session-access.md">ADR-0039</a></p>

## Context

ADR-0039 requires a per-session secret to access a token-required session, but leaves open what that secret *is* and how it ages out. The token must be issuable at session start, verifiable on every session request, and cheap to check. Two sub-questions: what backs the token (a stored secret vs a signed value), and when does it stop working.

## Decision

We will issue a **stateless signed token** using Django's signing (`django.core.signing`), carrying only the session's `external_id` under a dedicated salt. Nothing is stored in the database: the token is verified by signature and a match against the path session ID, and can be re-derived server-side for any session at any time (e.g. for bound-session pages rendered by Django views).

Expiry is **not** encoded in the token. Instead a global inactivity backstop, `CHAT_SESSION_TOKEN_INACTIVITY_WINDOW` (default 7 days), is checked at request time against the session's `last_activity_at` (falling back to creation time when there has been no activity). `last_activity_at` advances on user messages only, so polling cannot keep a leaked token alive.

## Consequences

- No token storage or lookup; verification is a signature check plus an ID comparison.
- Tokens are **not individually revocable** — the levers are flipping `session_token_required` or ending the session (revocable tokens would require revisiting this ADR).
- `SECRET_KEY` rotation is handled by Django via `SECRET_KEY_FALLBACKS`; an abrupt rotation without fallbacks invalidates live tokens, which the widget's 403-recovery path degrades to "a new conversation starts".
- The inactivity window is a coarse backstop, not a tight match for the widget's client-side persistence (which the server can't know); it only has to comfortably exceed any reasonable widget config while still ending "permanent" access.
- Bounding activity to user messages means a bot-only session with no user replies expires on its creation time.

## Alternatives considered

- **Opaque token stored as a hash on the session** — rejected: adds a column and a lookup, and its main benefit (per-session revocation) isn't needed yet.
- **Encode `max_age` in the token** — rejected: bakes the lifetime in at issue time and can't reflect later activity; server-side check against `last_activity_at` is the live signal.
- **Per-channel or per-widget configurable expiry** — rejected: the widget's persistence is set by the embedding site and unknowable server-side; a generous global backstop is simpler and sufficient.
- **No expiry (token valid for session lifetime)** — rejected: preserves indefinite access for a leaked token, the problem ADR-0039 set out to bound.
