# ADR-0067: Field order is presentation, stored outside the locked schema

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Pawan Verma · Created: 2026-09-21</p>

<p class="adr-meta">Extends: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a> (the schema lock it describes gains a presentation-only exemption; everything else stands)</p>

## Context

ADR-0015 stores `AnnotationQueue.schema` as a field-name-keyed jsonb map and
locks it once any item has a review, permitting only per-field `required` to
change. Postgres jsonb does not preserve key order — it re-sorts keys by length
then bytewise — so the order an author enters fields in has never been the order
reviewers see them in. Issue #4276 asks for author-controlled order, explicitly
including on locked queues.

## Decision

Field order is stored as `AnnotationQueue.field_order`, an `ArrayField` of field
names held **outside** `schema`. `FieldDefinition` is unchanged, so evaluator
`output_schema`, `pydantic_fields` and the LLM judge's output model are
unaffected.

A single resolver, `AnnotationQueue.ordered_field_names()`, is the only way
order is read. It is tolerant: names absent from `schema` are dropped and schema
keys absent from `field_order` are appended, so the two cannot disagree in a way
that loses or duplicates a field. The form is strict: a non-empty submitted
`field_order` must name exactly the schema's fields.

Re-ordering is exempt from the schema lock. The lock protects **what was
measured**; order is **how it is presented**, and `Annotation.data` is keyed by
field name, so re-sequencing cannot invalidate a stored submission. Because
order lives outside `schema`, `_validate_locked_schema_change` never sees it and
the exemption needs no carve-out.

## Consequences

- An empty or NULL `field_order` falls back to schema order, so existing queues
  render unchanged and no backfill migration was needed.
- The column is nullable because `AddField` drops the DB default after migrating,
  and the previous release's inserts omit the column during a rolling deploy.
- Evaluator `output_schema` keeps its jsonb-sorted order. Giving it author
  control would mean putting a presentation concept into the shared model that
  defines what is measured, and it has no field-builder UI to drive it.
- The items-table summary column's "first three fields" now means the author's
  first three; previously it was whichever three Postgres sorted first.
- A queue acquires an explicit order only when someone next saves the form.

## Alternatives considered

- **`order: int` on `BaseFieldDefinition`** — rejected. `human_annotations`
  borrows `FieldDefinition` from `apps.evaluations`; adding order there is the
  borrower mutating the lender's model for a need the lender does not have.
- **`schema` as an ordered list of named definitions** — rejected. jsonb arrays
  do preserve order, but the change breaks a stored contract read by aggregation,
  exports, score writers, concordance and tag rules, and requires migrating every
  existing row.
- **A separate compact re-order view** — rejected on UI grounds; controls on the
  existing field cards are sufficient at typical rubric sizes.
