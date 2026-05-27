# ADR-0005: Validate inbound email attachments by content sniffing

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0004-persist-inbound-email-attachments-in-handler.md">ADR-0004</a></p>

## Context

With inbound attachments persisted (ADR-0004), the bot accepts arbitrary files from anyone who can email a public address. The email `Content-Type` header and the filename extension are both sender-controlled and trivially spoofable — a shell script can claim `text/csv`, an executable can be named `report.pdf`. We need to keep executable, installer, and disk-image content out of team storage and out of the LLM's view, without an exhaustive allowlist that would reject legitimate but unusual file types.

We also need to decide what a user sees when their attachment is rejected. ADR-0002 established a no-auto-reply stance for the email channel to avoid bounce loops, and that constraint applies here too.

## Decision

We will validate each inbound attachment by sniffing its canonical type from the bytes with `python-magic`, then checking three independent signals — filename extension, claimed header type, and magic-detected type — against a shared denylist of executables, installers, and disk images; any single hit rejects the file (defense in depth). We additionally reject cross-category lies where the claimed and detected top-level types disagree, exempting `application/octet-stream` on either side (genuine "unknown") and a curated allowlist of textual `application/*` types (JSON, XML, YAML, …) that magic reports as `text/plain`. Script types are deliberately excluded from that allowlist, so "I'm a CSV but actually a shell script" fails. The magic-detected type becomes the canonical `content_type` stored on the File. Rejected attachments are not bounced; instead a bracketed note per rejection is appended to `message_text` so the LLM can tell the user.

## Consequences

- Spoofed extensions and headers cannot smuggle executables past the filter — the bytes are the source of truth, and the stored `content_type` reflects the real type.
- A denylist (not an allowlist) keeps unusual-but-benign types working; only a curated dangerous set is blocked.
- No bounce mail — consistent with ADR-0002's no-auto-reply stance; the user still learns the reason via the LLM.
- **Negative:** `python-magic` (libmagic) becomes a runtime dependency; detection is heuristic and can misclassify, occasionally rejecting a legitimate file on a category mismatch.
- **Negative:** the text-like allowlist needs maintenance as new textual `application/*` types appear.
- **Negative:** skip reasons reach the user only if the LLM relays them — there is no guaranteed out-of-band notification.

## Alternatives considered

- **Trust the email `Content-Type` header or filename extension:** rejected — both are sender-controlled and spoofable.
- **Allowlist of permitted types:** rejected — too restrictive; rejects legitimate unusual files and needs constant expansion.
- **Bounce or reply on rejection:** rejected — risks bounce loops (per ADR-0002) and adds out-of-band mail; a metadata note in `message_text` is simpler and safer.
