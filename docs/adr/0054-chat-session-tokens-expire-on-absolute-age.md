# ADR-0054: Chat session tokens expire on absolute age, not inactivity

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-08-14</p>

<p class="adr-meta">Supersedes: <a href="0040-stateless-signed-session-tokens-with-inactivity-expiry.md">ADR-0040</a> (its expiry rule; the stateless signed token stands)</p>

## Context

ADR-0039 decides admission once, at session creation. ADR-0040 then expired a session's token on dormancy: a sliding window measured from `last_activity_at`, which advances on every user message. Polling deliberately did not advance it, so a leaked token could not keep a session alive without talking — but a caller who keeps chatting slides the window forever.

The abuse budget of one admitted caller is its rate limit (ADR-0052, a per-session bucket) times its session lifetime. The second factor was unbounded, which made the first one moot: one admission bought chatbot usage at the throttle's rate, indefinitely.

## Decision

We will expire a session's token on **age**. It stops working `CHAT_SESSION_TOKEN_LIFETIME` (default 7 days) after the session was created; activity does not extend it, and `last_activity_at` is no longer read for expiry at all.

This replaces the inactivity window rather than joining it. Since `last_activity_at` is never earlier than `created_at`, any lifetime at or below the old window makes the dormancy branch unreachable. The two only compose when the lifetime is the longer of the two, which nothing wants today.

The lifetime is therefore mandatory. With the window gone, a session without one would have no expiry at all — the outcome ADR-0039 exists to prevent — so there is no "off".

The token mechanism from ADR-0040 is unchanged: a signature check plus a session-ID match, nothing stored, no expiry encoded in the token.

## Consequences

- 7 days is deliberately the old number, and the new rule fires no later than the old one for any session, so no session's life is extended.
- Sessions currently kept alive past a week by activity now end. An *unbound* widget's existing `session_expired` recovery starts a new conversation, so history stays on the old session and the participant stops seeing it.
- A *bound* widget — one handed a `session-id` and `session-token` by its host page, which is how the full-page and kiosk chat render — cannot restart a session it does not own. It surfaces "This chat session is no longer available" and the participant has to re-enter through the chatbot's start URL. Under the inactivity window this only befell an already-abandoned session; now a conversation still in daily use dies on its seventh day. Giving those pages a "start a new chat" affordance is the follow-up this decision owes them.
- Admission becomes recurring rather than one-shot — an expired caller goes back through session start and is re-admitted under whatever rules apply then, which is what makes a short-lived admission credential mean anything.
- One global value serves every channel, so a channel facing abuse cannot yet tighten below it; 7 days is a floor, not a defence.
- ADR-0040's care that polling must not advance `last_activity_at` stops mattering for access; the field still orders sessions in the UI.

## Alternatives considered

- **An absolute cap beside the inactivity window** — rejected: the window is unreachable at any lifetime at or below it, and dead code that reads as live policy is worse than no policy.
- **Per-channel `session_token_lifetime` override** — deferred, not rejected: it is the lever an abuse-facing channel actually needs (hours, not days), and slots in as a nullable field falling back to the global.
- **Per-session message budget** — rejected: redundant once the lifetime bounds rate × time, and it needs a counter where the lifetime needs nothing.
- **Per-request credential re-checks on session-bound endpoints** — rejected: a much larger change to ADR-0039's model for a small gain over this.
