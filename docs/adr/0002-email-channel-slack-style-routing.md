# ADR-0002: Slack-style routing priority chain for email channel

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0001-use-anymail-webhook-for-email-ingress.md">ADR-0001</a></p>

## Context

An inbound email must be routed to the correct experiment and, if one exists, the correct ongoing conversation. Slack's routing chain (`thread_ts` → `slack_channel_id` → `SLACK_ALL_CHANNELS` default) is a tested precedent. Email offers analogous signals: `In-Reply-To` / `References` headers (thread), the to-address (channel), and a global default for unsolicited mail.

We also need to handle the no-match case and identify the participant. Auto-replying on no-match can trigger bounce loops with another autoresponder. A single human may write from multiple addresses, but a richer identity model is out of scope for v1.

## Decision

We will route inbound email using a four-tier priority chain mirroring Slack:

1. `In-Reply-To` is looked up against `ExperimentSession.external_id`.
2. On a miss, each `Message-ID` in the `References` header is scanned in turn. RFC 2822 guarantees the root `Message-ID` appears in `References`, so replies to interior messages still resolve.
3. The to-address is matched against `ExperimentChannel.extra_data["email_address"]`, creating a new session.
4. Otherwise a channel with `extra_data["is_default"] == True` handles it — a global fallback checked across all teams, not team-scoped.

If even the default is absent we silently drop the message; never auto-reply on no-match. The participant identifier is the sender's email `addr_spec`; multi-address resolution is an accepted v1 limitation.

## Consequences

- **Positive:** Reuses the Slack routing mental model — same shape and order.
- **Positive:** Each tier hits either the indexed `external_id` column or a `JSONField` containment lookup on `extra_data` — no new tables.
- **Positive:** Silent-drop on no-match makes the system safe to expose to public addresses; autoresponder bounces can't loop.
- **Negative:** The same human with two addresses becomes two participants. Acceptable for v1.
- **Negative:** Silent-drop makes legitimate routing failures invisible to the sender; we rely on logs for debugging.

## Alternatives considered

- **Subject-line keyword parsing** (e.g. `[bot:foo]`) → rejected: fragile and breaks when a client renames a thread.
- **Separate inbound address per session** (`session-abc@chat...`) → rejected: exhausts the address space and degrades if the address is mangled in transit.
- **Auto-reply "no matching bot" on no-match** → rejected: risks infinite bounce loops with automated senders.
- **Multi-address identity resolution (Identity / claim flow)** → deferred: meaningful model surface, not needed to ship v1.
- **Per-channel custom routing functions** → rejected: needless flexibility; a single chain is easier to reason about.
