# ADR-0051: Pipeline discovery is an LLM-facing contract

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-08-07</p>

Extends: [ADR-0024](0024-inspect-denormalized-readonly-projection.md)

## Context

`GET /api/v2/pipeline/nodes/` and `GET /api/v2/pipeline/options/` exist so an agent can discover what
a pipeline may contain before writing one. Both reshape payloads the pipeline builder already
consumes, and the first cut passed those payloads through almost unchanged, explaining the
differences in prose on the endpoint description.

That prose was mostly exceptions. Option keys and the params reading them were named alike in most
cases and differently in a handful, so the description told the agent to infer the pairing "from
context". Only 40% of node params carried a `description`; the rest offered a `title` auto-derived
from the field name. The pairing rule between `llm_provider_id` and `llm_provider_model_id` was
stated nowhere, although violating it is a 400. Nothing said which node types fan out or how an edge
addresses an output. A human fills those gaps from the builder UI, from tooltips, from having seen a
pipeline before. An LLM has the response body and nothing else, and where the body is ambiguous it
guesses.

## Decision

We will treat these two endpoints as a contract read by a model with no other context, and encode in
the payload what the prose was explaining:

- **A param's options live under the `/pipeline/options/` key of the same name.** `source_material_id`
  draws from `source_material`, `collection_index_ids` from `collection_index`. The shared helper names
  every key after the param that reads it, so the rule holds rather than being restated on every param.
  It has one documented exception, below.
- **Prompt variables are keyed by prompt flavour, not by param name.** `prompt_variables` serves
  `template_string`, `llm_prompt_variables` serves an LLM node's `prompt`, `router_prompt_variables` a
  router's. The exception is forced: two different params are both called `prompt` and each accepts a
  different variable set, and the payload is one flat dict, so a key per param name would collide.
  `?node_type=` resolves which list applies. All three are renamed out of the builder's widget
  vocabulary (`jinja_node`, `text_editor_autocomplete_vars_*`).
- **Cross-param rules are data.** `must_match` (this value must agree with another param's chosen
  option on a named attribute), `options_keyed_by` (another param's choice selects which sub-list
  applies), `applies_when` and `requires_feature_flag` are emitted per param.
- **Node types declare their output topology** — how many outputs, and how an edge's `source_handle`
  names one — so discovery is sufficient to build a graph, not only to fill in params.
- **No namespaced vocabulary reaches the agent.** The two `ui:` keys carrying real meaning are
  renamed (`applies_when`, `requires_feature_flag`); every other key with a `namespace:` prefix is
  dropped, whether it is presentation (`ui:`) or an instruction about the payload (`api:`).
- **Every param on a listed node type carries a `description`**, enforced by
  `test_every_param_is_described`.
- **The list is exactly the types an agent may create.** Deprecated types and the server-managed
  structural ones (`StartNode`, `EndNode`, `Passthrough`) are absent rather than present behind a
  `can_add: false` flag — the endpoint answers "what can I build", so the answer is the list.
- **Option keys are snake_case** and errors carry a reason: a 404 lists the valid types, and
  distinguishes server-managed from unknown. Deprecation gets no branch — a deprecated type reads as
  unknown. Nothing the API serves reads the builder's deprecation vocabulary, which is rendered markup
  once a node declares a `docs_link`, and `valid_types` is the same answer either way.
- **A prompt variable is served with a description, not a value.** The builder emits these as
  `{"label": v, "value": v}`, where the two are always identical. A human reading an autocomplete
  dropdown infers what `temp_state` holds; a client cannot, so all three lists carry
  `{"label", "description"}` instead. `PROMPT_VAR_DESCRIPTIONS` must cover every variable any list
  offers, enforced by `TestPromptVarDescriptions` — a gap is a 500, not a missing field.
- **The options payload is a whitelist, not a subtraction.** It serves exactly the keys the listed
  node types reference, derived from their `ui:optionsSource` declarations, plus
  `API_ONLY_OPTION_KEYS`. A param the API withholds takes its option list with it, and so does a node
  type that stops being listed.
- **Every param that reads an option list says so on the field.** Four did not, because their widgets
  resolve options themselves and ignore `optionsSource` (`llm_provider_id`, `llm_provider_model_id`,
  `tool_config`, `synthetic_voice_id`). Since the whitelist and `?node_type=` scoping are both derived
  from those declarations, an undeclared param is one whose options a scoped response silently omits —
  so the declaration is now unconditional, and `test_scoping_covers_every_param_that_reads_an_option_list`
  holds it that way. The builder is unaffected: the widgets that ignore the key keep ignoring it.

Most of this lives in `apps/api/v2/discovery/`. The exception is the one decision that belongs to the
param itself: `UiSchema(api_exclude=True)` withholds a param, and the whitelist then withholds its
option list too. The shared helpers in `apps/pipelines/nodes/node_metadata.py` name every option key in
snake_case after the param that reads it, and both the builder and the API read that payload verbatim.

## Consequences

- An agent follows one naming rule to resolve any param's permitted values, plus a single stated
  exception for prompt variables, instead of a set of exceptions it has to recognise by name.
- The three prompt-variable lists are a standing hazard: a client that reads an LLM node's set and
  assumes a router accepts the same will write `{source_material}` into a router prompt, which fails
  validation. `?node_type=` is the intended way to fetch them, and
  `test_each_prompt_flavour_serves_its_own_variable_set` pins the three sets apart.
- Descriptions live on the pydantic `Field`, so the builder renders them as help text too.
- Two vocabularies now exist for the same data, and a new `ui:` key defaults to being dropped from
  the API. A param whose meaning depends on it must be added to `UI_KEY_TRANSLATIONS` deliberately.
- The name-matching rule is a convention, not a mechanism: renaming an option key without renaming
  the param that reads it breaks the join silently. `?node_type=` scoping derives the pairing from
  `ui:optionsSource`, and `test_every_key_a_node_type_scopes_to_is_actually_served` catches a dangling
  target.
- Withholding a new param from the API is a one-flag change on the field, not an edit to a set in
  another app. Because the payload is a whitelist, the param's option list follows automatically.
- `MUST_MATCH` and `OPTIONS_KEYED_BY` are hand-maintained and keyed by param name; a renamed param
  silently drops out of them.
- `?node_type=` lets an agent fetch only the options one node can reference, which is most of the
  payload for most nodes.
- A type read from an `/inspect/` response may not be in the list. Resolving it is a 404, which is
  why that body has to distinguish server-managed from unknown rather than lumping them together.
- That distinction is not extended to deprecated types, and the gap is accepted knowingly: an existing
  pipeline may still contain a deprecated node — deprecation only sets `can_add: false` — and
  `/inspect/` reports its `type` verbatim, so a client can read `AssistantNode` off one endpoint and be
  told by the other that no such type exists. `valid_types` still carries the corrective answer. The
  structural types keep their branch because they appear in every pipeline's `/inspect/` output, where
  a deprecated node is rare and getting rarer.

## Alternatives considered

- **Ship the structural types with `can_add: false`.** Rejected: it puts a type an agent must not
  create in the list of types it may create, behind one boolean among six fields. Their schemas
  offer nothing to act on — a single fixed `name` param, and `StartNode`/`EndNode` are `can_delete:
  false` and excluded from versioning.
- **Leave the four undeclared `optionsSource` links out and special-case them in the API.** Rejected:
  it puts the pairing in two places, one of which the field's author never sees. Declaring it on the
  field turned out not to touch the frontend at all, so the reason to avoid it did not hold.
- **Keep the exceptions in the endpoint description.** Rejected — prose an agent must remember is
  where the errors come from; the point of the endpoint is to remove guesswork.
- **Strip every `ui:*` key, including `ui:optionsSource`.** Rejected: that was the original plan and
  it would have severed the only machine-readable link between the two endpoints.
- **Serve one merged endpoint.** Rejected: node types are static per deploy and cacheable by ETag,
  options are team-scoped and change with the team's resources.
