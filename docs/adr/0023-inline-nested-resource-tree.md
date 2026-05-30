# ADR-0023: Inline nested resource tree for the inspect payload

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0022-inspect-denormalized-readonly-projection.md">ADR-0022</a></p>

## Context

The inspect projection ([ADR-0022](0022-inspect-denormalized-readonly-projection.md)) must convey a chatbot's pipeline, the resources each node references, and the chatbot's triggers. The primary consumer is an LLM-agent verifier reasoning over the JSON, for which locality matters more than wire size. The candidate representations were flat lookup tables keyed by id, a JSON:API compound document, and an inline nested tree.

## Decision

We will embed each referenced resource **inline** under a named key on the node or event that uses it, producing a self-contained tree read top-to-bottom with no pointer-chasing. Specifically:

- A provider + model pair is **flattened into a single object** under one concept key (`llm`, `voice`, `embedding`) — e.g. `llm = { provider_id, provider_name, type, model, max_token_limit, deprecated }`. Although provider and model are separate DB rows (joined by `type`, not a foreign key), they are selected together via a single combined widget, and a model is identified by type + name + token-limit so its DB id is not externally meaningful. Because this projection is not round-trippable ([ADR-0022](0022-inspect-denormalized-readonly-projection.md)), the shape is optimized for the consumer's view of effective config rather than the two-FK write structure.
- Chatbot triggers are walked as a top-level `events` block (with `static_triggers` and `timeout_triggers`) **in addition** to the pipeline nodes, because triggers attach to the chatbot, not the pipeline graph — a node-only walk would silently omit them.
- A `pipeline_start` event embeds its referenced pipeline using the **same** canonical Pipeline shape (`{ id, name, version_number, graph, nodes }`) used at the top level; this does not recurse, since a pipeline carries no triggers of its own.
- Wiring is conveyed by **containment** — a resource nested under a node is wired to that node — so no separate back-reference field is emitted.

## Consequences

- Single-pass readability; nodes are self-describing; wiring is implicit in structure.
- A resource referenced by many sites is duplicated (byte-for-byte identical, sharing the same `id`), so payload size is non-deterministic and grows with fan-out, and diffing two chatbots is harder than with a normalized table.
- The collector must still batch-load each resource type once to avoid N+1, then inline copies from the in-memory result.

## Alternatives considered

- Flat lookup tables keyed by id — rejected: forces the consumer to dereference and reads poorly in isolation.
- JSON:API compound document — rejected: verbose envelope and `{type, id}` linkage boilerplate for a read-only projection.
- Nesting provider and model as two distinct sub-objects (`{ provider, model }`) — rejected: the pair is selected as one in the UI and inspect is a projection ([ADR-0022](0022-inspect-denormalized-readonly-projection.md)), so a flat object better matches how a consumer reasons about effective config; the model's DB id carries no external meaning.
