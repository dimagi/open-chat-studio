---
status: extracted
---

# Re-ordering annotation queue fields — design

**Date:** 2026-09-21
**Tracking issue:** [#4276](https://github.com/dimagi/open-chat-studio/issues/4276)
**Extends:** [ADR-0015](../../adr/0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md)
(the schema lock it describes gains a presentation-only exemption)

## Summary

Give annotation queue authors control over the order their rubric fields are
presented in, and allow re-ordering even after the schema has locked.

Order is stored as a new `AnnotationQueue.field_order` list, deliberately
*outside* the locked `schema`. `FieldDefinition` and evaluator `output_schema`
are untouched.

## Problem

Issue #4276 states that "the fields are rendered according to the order they
appear in the schema". That is true only in the browser. `AnnotationQueue.schema`
is a `SanitizedJSONField` over Postgres **jsonb**, which re-sorts object keys by
length and then bytewise on write. Verified against a dev database:

```
authored  overall_quality, tone, accuracy, hallucination, escalate, notes
stored    tone, notes, accuracy, escalate, hallucination, overall_quality
```

The author's order is destroyed on save today, so no queue has ever displayed
the order its author chose. The headline question in the rubric sinks to the
bottom and the free-text field floats to second, purely as a function of name
length.

This makes the work *introducing* an order, not exposing one — the distinction
between adding drag handles and adding somewhere to keep the answer.

## Why re-ordering is safe on a locked queue

ADR-0015 locks `schema` once any item has a review (`items.filter(
review_count__gt=0).exists()`), on the grounds that "changing fields mid-queue
would silently invalidate prior submissions", and lists free-form schema edits as
a rejected alternative. Only per-field `required` stays mutable.

Re-ordering does not breach that reasoning. `Annotation.data` is keyed by field
name, so re-sequencing the display cannot invalidate a stored submission. The
lock protects **what was measured**; order is **how it is presented**. Keeping
order out of `schema` is what makes the exemption fall out mechanically rather
than requiring a carve-out in the lock validation.

## Decision: order belongs to the queue, not the field definition

`FieldDefinition` lives in `apps/evaluations/field_definitions.py` and is
consumed by `human_annotations` under ADR-0015's "reuse, not a fork" rule.
`output_schema` (`apps/evaluations/evaluators.py:109`) is declared over it.
The dependency for schema *shape* runs one way: evaluations owns the vocabulary,
human_annotations borrows it.

(There is an unrelated reverse import — `apps/evaluations/forms.py:39` pulls in
`AnnotationQueue` for the "build an eval dataset from a queue" picker at line
1152. It touches no schema.)

Putting `order` inside `FieldDefinition` would therefore mean the borrower
mutating the lender's model to satisfy a need the lender does not have. Every
evaluator's `output_schema` would gain an `order` key it never asked for;
`pydantic_fields`, which feeds the LLM judge's output model, would have to strip
it; and the locked-schema structural diff would need to exclude it the way it
already excludes `required`. The concept would live in `evaluations` and be used
only by `human_annotations`.

Scope is therefore **annotation queues only**. Evaluator `output_schema` keeps
its jsonb-sorted order; it has no field-builder UI to put a control in, and
nobody has asked for it.

## Data model

```python
# AnnotationQueue
field_order = ArrayField(models.CharField(max_length=255), default=list, blank=True, null=True)
```

`ArrayField(CharField(...), default=list, blank=True)` is the established idiom
for a list-of-strings column in this codebase (`apps/assistants/models.py:41`,
`apps/experiments/models.py:605`, `apps/custom_actions/models.py:48`).

### One resolver, strict on write and tolerant on read

A single model method becomes the only way anything reads field order:

```python
def ordered_field_names(self) -> list[str]:
    known = list(dict.fromkeys(n for n in (self.field_order or []) if n in self.schema))
    return known + [n for n in self.schema if n not in known]
```

Names in `field_order` that no longer exist in `schema` are dropped; schema keys
absent from `field_order` are appended. The two can never disagree in a way that
loses or duplicates a field, which matters because `schema` is writable from
paths the form does not control — shell, fixtures, cross-instance team import.

The form stays strict: a submitted `field_order` must match `schema` keys
exactly. Tolerance exists for rows the form did not write, not as a licence for
the form to write inconsistent ones.

### No backfill

When `field_order` is empty the resolver falls through to `schema`'s own
iteration order, which is exactly what every queue displays today. Existing
queues therefore render identically after deploy with **no data migration**.

This is deliberate on two counts. It avoids the AGENTS.md "ask first" gate on
data migrations that mutate existing rows — there is nothing to mutate. And a
queue acquires an explicit order only when someone next saves the form, which is
the first moment anyone had an opinion about it.

### Deploy safety

`null=True` is load-bearing. Migrations run to completion while the previous
release is still serving, and `AddField` drops the DB default afterwards, so a
non-null column would break the old release's inserts, which omit it. Nullable
costs nothing because `NULL` and `[]` are equivalent to the resolver. (`db_default`
is the alternative — it has precedent at `apps/teams/models.py:76` — but the
ArrayField syntax should be confirmed before relying on it.)

## Form and wire format

The Alpine component already holds `fields` as an ordered array, so both hidden
inputs serialise from that single source and cannot drift client-side:

```html
<input type="hidden" name="schema"      :value="schemaJson">
<input type="hidden" name="field_order" :value="fieldOrderJson">   <!-- new -->
```

`field_order` joins `AnnotationQueueForm` as a hidden `CharField(required=False)`.
`clean_field_order` parses the JSON and validates it is a list of strings.
Set-equality against `schema` keys belongs in `clean()`, where both cleaned
values are reliably available, rather than depending on field declaration order.

**`schemaJson` skips fields whose name is blank** (`queue_form.html:314`).
`fieldOrderJson` must skip them by exactly the same rule, or a half-typed new
field breaks set-equality and the form rejects a save the author believes is
valid.

No change is needed to the lock. A re-order on a locked queue re-emits the same
keys with the same definitions in a different insertion order;
`_validate_locked_schema_change` (`forms.py:101`) compares key *sets* and
per-field dicts, so it passes untouched.

## Read surfaces

Six places render fields in sequence. Ordering `get_field_definitions()` itself
is what keeps the change small — the reviewer's form, the surface that matters
most, then needs no edit at all.

| Surface | Change |
| --- | --- |
| `models.py:113` `get_field_definitions()` | Build in resolved order; dicts preserve insertion order |
| `forms.py:147` `build_annotation_form` | None — inherits order from the above |
| `views/annotate_views.py:92` prior-reviews panel | `queue.ordered_field_names()` in place of `list(queue.schema.keys())` |
| `views/export_views.py:145,156` CSV columns | Same swap |
| `queue_detail.html:70` aggregates panel | View supplies ordered `(field_name, stats)` pairs instead of the template iterating `aggregates.items` |
| `columns/annotations_summary.html:6` | Iterate ordered names; read values via the existing `get_item` filter (`apps/web/templatetags/default_tags.py:23`) |
| `remove_session_confirm.html:29` | Same; the view already has `queue` in context (`queue_views.py:478`) |

Two notes:

- `annotations_summary.html` reaches the queue through `record.queue`. The items
  table needs `select_related("queue")`, or the ordered names passed in as table
  context, or it is an N+1 per row. See `docs/agents/django_performance.md`.

`aggregation.py:45` also iterates `ann.data`, but only to accumulate into a
name-keyed dict. It is order-independent and needs no change.

The summary column deserves separate mention: its `forloop.counter <= 3` cap
currently means "three fields chosen by Postgres's key sort". It is a
user-visible bug in its own right, not mentioned on the issue, and this work
fixes it incidentally by making "the first three" mean something.

## UI design

Both affordances live on the existing field card. There is no separate re-order
view and cards are never collapsed.

- A drag grip at the card's leading edge, and ▲▼ buttons in the card header
  beside the existing delete button.
- `moveField(index, delta)` swaps within `fields`. ▲ is disabled at index 0, ▼ at
  the last index.
- Drag uses native HTML5 events on the grip — `@dragstart` records the index,
  `@dragover.prevent`, `@drop` splices and reinserts. There is no drag library in
  `package.json`, and the pipeline palette's React DnD is unreachable from an
  Alpine template.
- ▲▼ are the keyboard-accessible path, not a nicety — they are the reason the
  hand-rolled drag implementation does not need to solve keyboard access.
- The `x-for` is already keyed by `field._id` (`queue_form.html:33`), so
  re-ordering moves existing DOM nodes rather than rebuilding them. Half-typed
  input state and focus survive a move. That existing keying is why this stays
  simple.

### Locked queues

The grip and ▲▼ are **not** bound to `locked`; the delete button keeps its
`x-show="!locked"`. The re-order cluster gets the promotion styling the Required
checkbox already uses when locked — `bg-base-100 rounded-lg p-2 border
border-primary/30` with a `font-semibold` label (`queue_form.html:185-191`).

This follows an existing rule rather than inventing one: a locked card already
keeps exactly one control live and marks it with a primary-tinted box. Field
order becomes the second such control. The locked-schema notice at
`queue_form.html:28` is updated to say that order, as well as `required`, stays
editable.

## Testing

Tests first, per the repo's TDD rule, parametrised with readable ids via
`pytest.param(..., id=...)`.

- **Resolver** — honours `field_order`; drops names no longer in `schema`;
  appends schema keys absent from `field_order`; `None` and `[]` both fall back
  to schema order.
- **Form** — the headline guard: a re-order saves successfully on a locked queue.
  Plus rejection of a `field_order` that does not match schema keys, and a
  blank-named in-progress field not breaking set-equality.
- **Read paths** — export column order, annotation form order, prior-reviews
  order and aggregates panel order all follow `field_order`.

Beyond green tests, exercise the builder in a running app per AGENTS.md
"Verifying changes": re-order on both an unlocked and a locked queue, save, and
confirm the reviewer's form and the CSV export agree.

## ADR to write

A new ADR extending ADR-0015, in the style of
[ADR-0055](../../adr/0055-binary-field-type-extends-the-field-definition-union.md)
(`Extends:` meta line — AGENTS.md forbids editing an accepted ADR): *field order
is presentation, stored outside the locked schema*. It records why order sits on
`AnnotationQueue` rather than in `FieldDefinition`, the resolver's
strict-write/tolerant-read split, and the lock exemption — closing the gap
ADR-0015 left when it said only `required` stays mutable.

## Out of scope

- Evaluator `output_schema` ordering, and any evaluator-side field builder.
- Collapsing or restyling the field cards beyond adding the re-order controls.
- Changing what the schema lock permits beyond order.
- Re-ordering choices *within* a `choice` field's option list.

## Alternatives considered

- **`order: int` on `BaseFieldDefinition`** — rejected. Pushes a presentation
  concept into the shared model that defines what is measured, reaching evaluator
  `output_schema`, `pydantic_fields` and the locked-schema diff for no
  evaluator-side benefit. See "Decision" above.
- **`schema` as a list of named definitions** — rejected. JSON arrays do preserve
  order in jsonb, making this the most direct fix, but it breaks a stored
  contract read by aggregation, exports, score writers, concordance and tag rules,
  and requires migrating every existing row.
- **Up/down buttons only** — considered and defensible for 3–10 field rubrics, but
  moving the last field to first is five clicks with the page jumping under the
  cursor as tall cards swap.
- **Drag handle only** — rejected. Needs a keyboard fallback regardless, which
  means building the buttons anyway.
- **A compact re-order list (dialog, inline panel, or collapsed cards)** —
  explored in mockups and rejected. It solves dragging tall cards, but introduces
  a mode and a second representation of the field list. Controls on the card are
  enough at this rubric size.
