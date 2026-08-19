# ADR-0055: Binary field type extends the field definition union

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Barry Tandy · Created: 2026-08-17</p>

<p class="adr-meta">Extends: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a> (the field definition union it describes gains a fifth member; everything else stands)</p>

## Context

ADR-0015 describes `AnnotationQueue.schema` as mapping field names to entries of
"the int/float/choice/string union `apps.evaluations` uses for evaluator output".
Issue #2726 adds a boolean-style assessment ("was the answer correct") that judges
and annotators answer with one of two values and that aggregates as a rate rather
than a distribution. Modelling it as a two-choice `choice` field leaves polarity
undefined (which choice counts as true) and aggregates as mode/distribution.

## Decision

The `FieldDefinition` union gains a fifth member, `BinaryFieldDefinition`
(`type: "binary"`), with two display labels (`true_label`, `false_label`,
defaulting to True/False; non-blank and distinct). The stored value is the
integer `1`/`0` everywhere - results, Scores, aggregates, tag-rule conditions,
CSV exports. Labels are display vocabulary only, resolved from the schema at
render time, and reach the LLM judge folded into the field description
("1 = correct, 0 = incorrect") rather than as schema keywords.

Binary fields aggregate through a separate schema-dispatched function,
`aggregate_binary_field`, emitting `{"type": "binary", "count", "mean",
"true_count"}` (plus `excluded_count` for out-of-range values). The
value-dispatched `aggregate_field` (ADR-0017) is unchanged; callers choose the
function from the schema and fall back to `aggregate_field` for fields the
schema no longer names.

## Consequences

- Renaming or swapping labels is free for stored data but changes what
  historical values display as - the stored integers carry no vocabulary -
  except `Score.value_string`, which snapshots the label at write time so
  historical concordance rows keep the vocabulary in force when they were
  scored. This is the reverse of `choice` (stored strings, rename orphans
  history).
- Existing two-choice `choice` fields are not converted or retroactively
  rendered as rates (declined on the issue).
- A schema containing a binary field does not validate on instances that
  predate the union member; cross-instance team import carries it as an
  opaque blob and fails only where the union is parsed.
