# ADR-0002: Slack-style routing priority chain for email channel

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0001-use-anymail-webhook-for-email-ingress.md">ADR-0001</a></p>

## Context

An inbound email must be routed to (a) the correct experiment and (b) the correct ongoing conversation if one already exists. The system already has working channels for Slack, Telegram, and WhatsApp; Slack's routing chain (`thread_ts` → `slack_channel_id` → `SLACK_ALL_CHANNELS` default) in `apps/slack/slack_listeners.py:get_experiment_channel()` is a tested precedent. Email surfaces analogous signals: `In-Reply-To` / `References` headers (thread analog), the to-address (channel analog), and a per-team "default" channel for unsolicited mail.

We also need to decide what to do when nothing matches, and how to identify the participant. Sending an automated reply on no-match can trigger bounce loops against another autoresponder. Identity is non-trivial because a single human can write from multiple addresses, but a richer identity model is out of scope for v1.

## Decision

We will route inbound email using a four-tier priority chain that mirrors Slack: (1) the `In-Reply-To` header is looked up against `ExperimentSession.external_id`; (2) if that misses, each `Message-ID` in the `References` header is scanned in turn; (3) the to-address is matched against `ExperimentChannel.extra_data["email_address"]`, creating a new session; (4) if no channel matches the to-address, a channel with `extra_data["is_default"] == True` for the team handles it. If even the default is absent we silently drop the message — never auto-reply on no-match. The participant identifier is the sender's email `addr_spec`; multi-address resolution is an accepted v1 limitation.

## Consequences

- **Positive:** Routing reuses the mental model contributors already know from Slack; the priority chain is the same shape and order.
- **Positive:** Each tier hits either an indexed column (`external_id`) or a `JSONField` containment lookup on `extra_data` — cheap, no new tables.
- **Positive:** Silent-drop on no-match makes the system safe to expose to public addresses; bounces from autoresponders can't loop us.
- **Positive:** The `References` fallback handles replies to older messages in a thread, where `In-Reply-To` points at an interior `Message-ID`. RFC 2822 guarantees the root `Message-ID` appears in `References`, so the scan succeeds.
- **Negative:** Address fuzziness — the same human with two addresses is two participants. Acceptable for v1.
- **Negative:** Silent-drop makes legitimate routing failures invisible to the sender. We rely on logs for debugging.

## Alternatives considered

- **Subject-line keyword parsing** (e.g. `[bot:foo]` à la Mailman): rejected — fragile, hostile UX, broken the moment a client renames a thread.
- **Separate inbound address per session** (`session-abc@chat...`): rejected — exhausts the address space, complicates DNS, and degrades poorly if the address is mangled in transit.
- **Auto-reply "no matching bot" on no-match:** rejected — risks infinite bounce loops with automated senders.
- **Multi-address identity resolution (Identity / claim flow):** deferred — meaningful model surface; not needed to ship v1.
- **Per-channel custom routing functions (instead of a fixed priority chain):** rejected — needless flexibility; the Slack precedent works, and a single chain is easier to reason about.
