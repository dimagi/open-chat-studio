# ADR-0040: Fail-closed session-token enforcement rollout

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-09</p>

<p class="adr-meta">Extends: <a href="0038-require-proof-of-possession-for-chat-session-access.md">ADR-0038</a></p>

## Context

ADR-0038 introduced token enforcement, but it has to roll out over live traffic and existing clients. The chat poll endpoint returns messages for *any* session found by `external_id` — including sessions from every creation path (chat API, server-rendered web chat, channel platforms like Telegram) — so a default that forgets to protect one path leaks transcripts. Meanwhile widgets already embedded on customer sites predate tokens and would break if enforcement were unconditional.

## Decision

We will gate enforcement on a per-session boolean `ExperimentSession.session_token_required`, defaulting to **`True`** (fail closed) so every new session from every creation path is protected by default. Opting out is explicit:

- The session-start request accepts `use_session_token`; `false` creates an unprotected session and returns no token, `true` enforces.
- When the field is **absent**, enforcement is the default **unless** the request carries an `x-ocs-widget-version` header — a pre-token widget (every token-aware client sends the field explicitly), which is implicitly opted out. No version-number threshold is needed.

Existing rows are backfilled at migration time by activity: sessions inactive for more than 24 hours become `True` (their transcripts are protected immediately; authenticated participants retain access), while recently-active sessions become `False` so live conversations are not interrupted.

## Consequences

- New sessions are protected by default regardless of creation path — the safe failure direction.
- **Breaking change** for direct API consumers of the start endpoint that ignore the response: they must send the returned token or pass `use_session_token: false`. Requires changelog and API-docs updates.
- Old embedded widgets keep working unchanged via the header sniff; the sniff is temporary and removable once pre-token widgets age out.
- Spoofing the `x-ocs-widget-version` header only weakens the attacker's *own new* session; it cannot downgrade an existing session.
- The backfill ships before the token-aware widget: an old widget configured to resume sessions older than 24h could hit unrecoverable 403s. Default-config widgets discard sessions after 24h, bounding the blast radius; accepted.

## Alternatives considered

- **Opt-in default (`False`)** — rejected: fails open, leaving new sessions on non-widget paths exposed unless every caller remembers to enable protection.
- **Hard cutover with no opt-out** — rejected: breaks every embedded widget older than the token release until sites upgrade.
- **Version-number threshold for the widget sniff** — rejected: presence of the field vs the header already separates old from new clients; a hardcoded version is extra coupling.
- **Backfill everything to `True`** — rejected: would 403 in-flight conversations whose clients can't yet send tokens.
- **Store the enforcement flag in session/chat metadata JSON** — rejected: `session.state` is mutable by pipeline actions, so a bot config (or prompt injection) could clear the security flag; a dedicated column can't be.
