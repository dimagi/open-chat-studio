# ADR-0023: Inline nested resource tree for the inspect payload

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>

## Context

The inspect projection ([ADR-0022](0022-inspect-denormalized-readonly-projection.md)) must convey a chatbot's pipeline, the resources each node references, and the chatbot's triggers. The primary consumer is an LLM-agent verifier reasoning over the JSON, for which locality matters more than wire size. The candidate representations were flat lookup tables keyed by id, a JSON:API compound document, and an inline nested tree.

## Decision

We will embed each referenced resource **inline** under a named key on the node or event that uses it, producing a self-contained tree read top-to-bottom with no pointer-chasing. Specifically:

- A provider + model pair is grouped under one concept key (`llm`, `voice`, `embedding`) but kept as two distinct sub-objects, because a model is an independent catalog row joined to a provider by `type`, not owned by it.
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
- A true provider+model merge into one object — rejected: implies an ownership that does not exist and diverges from the likely write shape (two separate references).
