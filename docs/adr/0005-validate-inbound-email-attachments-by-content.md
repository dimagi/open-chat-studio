# ADR-0005: Validate inbound email attachments by content sniffing

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0004-persist-inbound-email-attachments-in-handler.md">ADR-0004</a></p>

## Context

With inbound attachments persisted ([ADR-0004](0004-persist-inbound-email-attachments-in-handler.md)), the bot accepts arbitrary files from anyone who can email a public address. Both the email `Content-Type` header and the filename extension are sender-controlled and spoofable. We need to keep executables, installers, and disk images out of team storage and the LLM's view, without an exhaustive allowlist that rejects legitimate but unusual types.

On rejection, [ADR-0002](0002-email-channel-slack-style-routing.md)'s no-auto-reply stance for the email channel (to avoid bounce loops) applies.

## Decision

We will sniff each attachment's canonical type from its bytes with `python-magic` and reject dangerous files, with no bounce reply:

- Check three signals — filename extension, claimed header type, and magic-detected type — against a shared denylist of executables, installers, and disk images; any single hit rejects the file.
- Reject cross-category lies where the claimed and detected top-level types disagree, exempting `application/octet-stream` on either side (genuine "unknown") and a curated allowlist of textual `application/*` types (JSON, XML, YAML, …) that magic reports as `text/plain`. Script types are excluded from that allowlist, so "I'm a CSV but actually a shell script" fails.
- Store the magic-detected type as the canonical `content_type` on the File.
- On rejection, append a bracketed note per attachment to `message_text` so the LLM can tell the user; do not bounce.

## Consequences

- Spoofed extensions and headers cannot smuggle executables past the filter; the stored `content_type` reflects the real type.
- A denylist keeps unusual-but-benign types working; only a curated dangerous set is blocked.
- No bounce mail, consistent with [ADR-0002](0002-email-channel-slack-style-routing.md); the user learns the reason via the LLM.
- **Negative:** `python-magic` (libmagic) becomes a runtime dependency, and its heuristic detection can misclassify, occasionally rejecting a legitimate file on a category mismatch.
- **Negative:** the text-like allowlist needs maintenance as new textual `application/*` types appear.
- **Negative:** skip reasons reach the user only if the LLM relays them; there is no guaranteed out-of-band notification.

## Alternatives considered

- **Trust the email `Content-Type` header or filename extension** → rejected; both are sender-controlled and spoofable.
- **Allowlist of permitted types** → rejected; too restrictive and needs constant expansion.
- **Bounce or reply on rejection** → rejected; risks bounce loops (per [ADR-0002](0002-email-channel-slack-style-routing.md)) and a `message_text` note is simpler.
