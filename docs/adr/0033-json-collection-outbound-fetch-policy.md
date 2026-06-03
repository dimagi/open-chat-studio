# ADR-0033: Outbound fetch policy for the JSON collection loader — uniform optional auth, SSRF validation, size cap

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-03</p>

<p class="adr-meta">Extends: <a href="0031-json-collection-document-source-loader.md">ADR-0031</a></p>

## Context

The loader fetches a user-configured JSON feed URL and then follows attachment links discovered inside that feed. Both are operator- or feed-controlled URLs that can point anywhere, including internal hosts or very large files. Some feeds require credentials; many are public. We had to decide how to authenticate and how to bound the risk of fetching arbitrary URLs.

## Decision

We will fetch with a single optional auth provider applied uniformly, validate every URL against the platform's user-input URL guard, and stream responses under a size cap.

- A single optional `AuthProvider` (types: bearer, basic, api_key; CommCare excluded) supplies headers applied identically to the JSON feed request and every attachment download. Left blank, requests go out anonymously, supporting public feeds.
- Every fetched URL — the feed and each attachment — is validated against the platform's user-input URL guard (strict outside `DEBUG`) before the request. A rejected URL raises and is handled by the failure-isolation rule in ADR-0032.
- Responses are streamed and rejected once they exceed a 50 MB cap, applied to both the feed and each attachment.

## Consequences

- The same credentials reach every host the feed links to, including cross-host attachment URLs; an operator needing different scopes per host must split into separate document sources or stage data behind a proxy.
- Attachment links come from feed content rather than direct user input, yet are still SSRF-validated at fetch time, so a compromised feed cannot redirect the loader at internal hosts.
- The size cap bounds memory and guards against hostile or runaway downloads, at the cost of dropping legitimately huge attachments.

## Alternatives considered

- **Per-host or per-attachment credentials** → rejected; v1 uses one provider for the whole source, with split-source or a proxy for finer scoping.
- **Including CommCare among allowed auth types** → rejected; excluded for this loader.
- **Validating only the feed URL at form time, trusting attachment links** → rejected; attachment links are feed-controlled and equally untrusted, so they are validated at fetch time too.
- **Unbounded `httpx.get`** (the original design) → rejected; streaming under a 50 MB cap prevents memory blowups from large or hostile responses.
