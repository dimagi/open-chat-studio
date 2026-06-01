# ADR-0028: Inspect authorizes on chatbot view + team scope, not per-resource permissions

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0021-invest-in-api-surface-not-readonly-role.md">ADR-0021</a>, <a href="0024-inspect-denormalized-readonly-projection.md">ADR-0024</a></p>

## Context

The inspect projection ([ADR-0024](0024-inspect-denormalized-readonly-projection.md)) embeds resources owned by many apps — collections, files, custom actions, providers, assistants, scheduled messages — each of which has its own model-level view permission. The endpoint reuses the chatbot view permission and a team-scoped `read_only` key ([ADR-0021](0021-invest-in-api-surface-not-readonly-role.md)). This raises the question of whether inspect must also enforce each embedded resource's own view permission.

## Decision

We will authorize inspect solely on the chatbot `view` permission plus team scope, and deliberately **not** enforce per-resource view permissions on the embedded resources. This is sound because every embedded resource is team-scoped and already co-visible to anyone who can view the chatbot — there is no intra-team gating that hides a chatbot's collections, actions, or providers from a user who can view the chatbot. Separately, the resource collector **must** team-scope every batch load, because the resource ids originate in untrusted node-parameter JSON; a stray or crafted cross-team id must resolve to absent rather than leak.

## Consequences

- Authorization stays simple — no per-resource permission plumbing — and matches the co-visibility users already have in the UI.
- Inspect transitively widens a chatbot-`view`-only key into "view everything this chatbot references"; this is documented so it reads as a deliberate choice in security review.
- The collector must scope loads three ways depending on the model: a direct team foreign key, indirectly via the owning chatbot, or none for genuinely global rows.

## Alternatives considered

- Enforce each embedded resource's own view permission — rejected: there is no intra-team gating to enforce, so it adds plumbing for no security gain.
- A dedicated `inspect` permission distinct from `view` — deferred: add later only if least-privilege inspect-only tokens become a real requirement.
