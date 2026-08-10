# ADR-0051: Pipeline discovery is an LLM-facing contract

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-08-07</p>

Extends: [ADR-0024](0024-inspect-denormalized-readonly-projection.md)

## Context

`GET /api/v2/pipeline/nodes/` and `GET /api/v2/pipeline/options/` exist so an agent can discover what
a pipeline may contain before writing one. Both reshape payloads the pipeline builder already
consumes, and the first cut passed those payloads through almost unchanged, explaining the
differences in prose on the endpoint description.

That prose was mostly exceptions. Four params had no declared link to their option list and the
description told the agent to infer it "from context". Only 40% of node params carried a
`description`; the rest offered a `title` auto-derived from the field name. The pairing rule between
`llm_provider_id` and `llm_provider_model_id` was stated nowhere, although violating it is a 400.
Nothing said which node types fan out or how an edge addresses an output. A human fills those gaps
from the builder UI, from tooltips, from having seen a pipeline before. An LLM has the response body
and nothing else, and where the body is ambiguous it guesses.

## Decision

We will treat these two endpoints as a contract read by a model with no other context, and encode in
the payload what the prose was explaining:

- **The `options_source` join is total.** Every param drawing from a fixed set names the
  `/pipeline/options/` key holding its values. Where the builder declares no `ui:optionsSource`
  because it hard-codes a widget, the API synthesises the link rather than leaving it to inference.
- **Cross-param rules are data.** `must_match` (this value must agree with another param's chosen
  option on a named attribute), `options_keyed_by` (another param's choice selects which sub-list
  applies), `applies_when` and `requires_feature_flag` are emitted per param.
- **Node types declare their output topology** — how many outputs, and how an edge's `source_handle`
  names one — so discovery is sufficient to build a graph, not only to fill in params.
- **No `ui:` vocabulary reaches the agent.** The two keys carrying real meaning are renamed
  (`options_source`, `applies_when`); presentation keys are dropped.
- **Every param on a listed node type carries a `description`**, enforced by
  `test_every_param_is_described`.
- **The list is exactly the types an agent may create.** Deprecated types and the server-managed
  structural ones (`StartNode`, `EndNode`, `Passthrough`) are absent rather than present behind a
  `can_add: false` flag — the endpoint answers "what can I build", so the answer is the list.
- **Option keys are snake_case** and errors carry a reason: a 404 lists the valid types, and says
  whether the name was deprecated, server-managed, or genuinely unknown.

All of this lives in `apps/api/v2/discovery.py`. The shared helpers in
`apps/pipelines/node_options.py` stay as the builder needs them, so the builder's payload — mixed-case
option keys, `ui:*` schema keys — is unchanged.

## Consequences

- An agent follows one rule to resolve any param's permitted values, instead of one rule plus four
  exceptions it has to recognise by name.
- Descriptions live on the pydantic `Field`, so the builder renders them as help text too.
- Two vocabularies now exist for the same data, and a new `ui:` key defaults to being dropped from
  the API. A param whose meaning depends on it must be added to `UI_KEY_TRANSLATIONS` deliberately.
- `IMPLIED_OPTIONS_SOURCE`, `MUST_MATCH` and `OPTIONS_KEYED_BY` are hand-maintained maps keyed by
  param name; a renamed param silently loses its link. `test_every_options_source_resolves_to_an_options_key`
  catches a dangling target but not a dropped one.
- `?node_type=` lets an agent fetch only the options one node can reference, which is most of the
  payload for most nodes.
- A type read from an `/inspect/` response may not be in the list. Resolving it is a 404, which is
  why that body has to distinguish server-managed from unknown rather than lumping them together.

## Alternatives considered

- **Ship the structural types with `can_add: false`.** Rejected: it puts a type an agent must not
  create in the list of types it may create, behind one boolean among six fields. Their schemas
  offer nothing to act on — a single fixed `name` param, and `StartNode`/`EndNode` are `can_delete:
  false` and excluded from versioning.
- **Declare the four missing links in `UiSchema` so builder and API agree.** Rejected for now: it
  changes the builder's schema for widgets that ignore `optionsSource`, widening a read-only ticket
  into a frontend change.
- **Keep the exceptions in the endpoint description.** Rejected — prose an agent must remember is
  where the errors come from; the point of the endpoint is to remove guesswork.
- **Strip every `ui:*` key, including `ui:optionsSource`.** Rejected: that was the original plan and
  it would have severed the only machine-readable link between the two endpoints.
- **Serve one merged endpoint.** Rejected: node types are static per deploy and cacheable by ETag,
  options are team-scoped and change with the team's resources.
