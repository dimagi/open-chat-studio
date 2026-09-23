# Annotation Queue Field Re-ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let annotation queue authors control the order their rubric fields are displayed in, including on queues whose schema has locked.

**Architecture:** Field order is stored as a new `AnnotationQueue.field_order` list, deliberately outside the locked `schema` jsonb. A single model resolver, `ordered_field_names()`, reconciles that list against the current schema and becomes the only way anything reads field order. Because order lives outside `schema`, the existing lock validation never sees it and re-ordering a locked queue works with no carve-out.

**Tech Stack:** Django 5.x, PostgreSQL (`ArrayField`), pydantic v2 (`FieldDefinition`), Alpine.js in a Django template, daisyUI/Tailwind, pytest + FactoryBoy.

**Spec:** `docs/superpowers/specs/2026-09-21-annotation-field-reordering-design.md`

## Global Constraints

- PostgreSQL only. `ArrayField` and jsonb behaviour may be relied on.
- Migrations must run correctly against **both** the old and new code — the deploy applies migrations while the previous release is still serving. `field_order` is therefore `null=True`; do not make it non-null.
- **No data migration.** Existing queues must render identically after deploy. An empty or NULL `field_order` falls back to schema order.
- `FieldDefinition` (`apps/evaluations/field_definitions.py`) and evaluator `output_schema` must not be modified. Scope is annotation queues only.
- Tests use `pytest.mark.parametrize` with readable ids via `pytest.param(..., id="...")` rather than enumerated data.
- Lint/format with `uv run inv ruff --paths <files>`; type check with `uv run inv typecheck`.
- Django template comments must use `{% comment %}{% endcomment %}`, never multi-line `{# #}`.
- Do not add explanatory comments to migrations.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `apps/human_annotations/models.py` | `field_order` column; `ordered_field_names()` resolver; ordered `get_field_definitions()` | 1 |
| `apps/human_annotations/migrations/0005_annotationqueue_field_order.py` | Add the column, nullable | 1 |
| `apps/human_annotations/forms.py` | Accept, validate and persist `field_order` | 2 |
| `apps/human_annotations/views/annotate_views.py` | Prior-reviews panel order | 3 |
| `apps/human_annotations/views/export_views.py` | CSV and JSONL column order | 3 |
| `apps/human_annotations/views/queue_views.py` | Pass ordered aggregate pairs to the detail template | 4 |
| `templates/human_annotations/queue_detail.html` | Render ordered aggregate pairs | 4 |
| `templates/human_annotations/columns/annotations_summary.html` | Render first three fields in author order | 4 |
| `templates/human_annotations/remove_session_confirm.html` | Render annotation values in author order | 4 |
| `templates/human_annotations/queue_form.html` | Grip + ▲▼ controls, `moveField`, drag handlers, `fieldOrderJson`, locked promotion | 5 |
| `docs/adr/0068-field-order-is-presentation-outside-the-locked-schema.md` | Record the decision | 6 |
| `docs/adr/index.md` | Index row for ADR-0068 | 6 |

Tests live in the existing `apps/human_annotations/tests/` files (`test_models.py`, `test_forms.py`, `test_views.py`) — this app keeps tests per layer, not per feature. Follow that.

---

### Task 1: `field_order` column and the order resolver

Adds the storage and the single function everything else will read order through. Nothing user-visible changes yet: with no `field_order` set, the resolver returns exactly today's order.

**Files:**
- Modify: `apps/human_annotations/models.py` (imports; `AnnotationQueue.schema` block around line 76; `get_field_definitions` at line 113)
- Create: `apps/human_annotations/migrations/0005_annotationqueue_field_order.py`
- Test: `apps/human_annotations/tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `AnnotationQueue.field_order: list[str] | None`
  - `AnnotationQueue.ordered_field_names() -> list[str]`
  - `AnnotationQueue.get_field_definitions() -> dict[str, FieldDefinition]` — same signature as today, now ordered.

- [ ] **Step 1: Write the failing test**

Append to `apps/human_annotations/tests/test_models.py`. Field names are single characters so that jsonb's (length, bytewise) key sort and the authored order coincide — that keeps the fallback cases deterministic after `refresh_from_db()`.

```python
@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field_order", "expected"),
    [
        pytest.param(["b", "a", "c"], ["b", "a", "c"], id="explicit-order-is-honoured"),
        pytest.param([], ["a", "b", "c"], id="empty-falls-back-to-schema-order"),
        pytest.param(None, ["a", "b", "c"], id="null-falls-back-to-schema-order"),
        pytest.param(["c", "removed"], ["c", "a", "b"], id="names-not-in-schema-are-dropped"),
        pytest.param(["c"], ["c", "a", "b"], id="schema-keys-missing-from-order-are-appended"),
    ],
)
def test_ordered_field_names(team, field_order, expected):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Ordered",
        schema={
            "a": {"type": "string", "description": "A"},
            "b": {"type": "string", "description": "B"},
            "c": {"type": "string", "description": "C"},
        },
        field_order=field_order,
        created_by=team.members.first(),
    )
    queue.refresh_from_db()

    assert queue.ordered_field_names() == expected


@pytest.mark.django_db()
def test_get_field_definitions_follows_field_order(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Ordered defs",
        schema={
            "a": {"type": "string", "description": "A"},
            "b": {"type": "int", "description": "B"},
        },
        field_order=["b", "a"],
        created_by=team.members.first(),
    )
    queue.refresh_from_db()

    assert list(queue.get_field_definitions()) == ["b", "a"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest apps/human_annotations/tests/test_models.py -k "ordered_field_names or field_definitions_follows" -v`

Expected: FAIL — `TypeError: AnnotationQueue() got unexpected keyword arguments: 'field_order'`.

- [ ] **Step 3: Add the column to the model**

In `apps/human_annotations/models.py`, add the import near the other Django imports:

```python
from django.contrib.postgres.fields import ArrayField
```

Then add the field to `AnnotationQueue`, directly after the existing `schema` declaration:

```python
    field_order = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        null=True,
        help_text="Field names in display order; names not listed fall back to schema order",
    )
```

`null=True` is required for deploy safety — see Global Constraints.

- [ ] **Step 4: Add the resolver and order `get_field_definitions`**

Replace the existing `get_field_definitions` method (currently at `models.py:113`) with these two methods:

```python
def ordered_field_names(self) -> list[str]:
    """Field names in display order, reconciled against the current schema."""
    known = list(dict.fromkeys(name for name in (self.field_order or []) if name in self.schema))
    return known + [name for name in self.schema if name not in known]


def get_field_definitions(self) -> dict[str, FieldDefinition]:
    """Parse the raw JSON schema into typed FieldDefinition objects, in display order."""
    adapter = TypeAdapter(FieldDefinition)
    return {name: adapter.validate_python(self.schema[name]) for name in self.ordered_field_names()}
```

- [ ] **Step 5: Create the migration**

Run: `uv run python manage.py makemigrations human_annotations -n annotationqueue_field_order`

Verify the generated `apps/human_annotations/migrations/0005_annotationqueue_field_order.py` matches:

```python
import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("human_annotations", "0004_backfill_authoritative"),
    ]

    operations = [
        migrations.AddField(
            model_name="annotationqueue",
            name="field_order",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=255),
                blank=True,
                default=list,
                help_text="Field names in display order; names not listed fall back to schema order",
                null=True,
                size=None,
            ),
        ),
    ]
```

There must be exactly one `AddField` and **no** `RunPython`. If a data migration appears, something is wrong — stop and re-read the Global Constraints.

- [ ] **Step 6: Apply the migration and run the tests**

```bash
uv run python manage.py migrate human_annotations
uv run pytest apps/human_annotations/tests/test_models.py -v
```

Expected: PASS, including the pre-existing `test_queue_get_field_definitions`.

- [ ] **Step 7: Lint and type check**

```bash
uv run inv ruff --paths apps/human_annotations/models.py apps/human_annotations/tests/test_models.py
uv run inv typecheck --python --paths apps/human_annotations
```

- [ ] **Step 8: Commit**

```bash
git add apps/human_annotations/models.py apps/human_annotations/migrations/0005_annotationqueue_field_order.py apps/human_annotations/tests/test_models.py
git commit -m "$(cat <<'EOF'
Add AnnotationQueue.field_order and the order resolver

Order is stored outside the locked schema jsonb, which re-sorts keys by
length then bytewise. An empty or NULL field_order falls back to schema
order, so existing queues render identically with no backfill.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Accept and validate `field_order` on the queue form

Makes the order round-trip through the edit form, and proves the headline requirement: re-ordering succeeds on a locked queue.

**Files:**
- Modify: `apps/human_annotations/forms.py` (`AnnotationQueueForm`, lines 36-118)
- Test: `apps/human_annotations/tests/test_forms.py`

**Interfaces:**
- Consumes: `AnnotationQueue.field_order` and `ordered_field_names()` from Task 1.
- Produces: `AnnotationQueueForm` accepting a `field_order` POST value — a JSON array of field-name strings. Empty or absent is valid and stores `[]`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_forms.py`. Note the existing imports at the top of that file already cover `json`, `AnnotationQueueForm`, `AnnotationQueue` and the factories; add `Annotation`, `AnnotationStatus`, `AnnotationItemFactory` only if not already imported.

```python
def _queue_form_data(schema, field_order=None, **overrides):
    """POST payload matching what the Alpine builder submits."""
    data = {
        "name": "Test Queue",
        "description": "",
        "schema": json.dumps(schema),
        "num_reviews_required": 1,
    }
    if field_order is not None:
        data["field_order"] = json.dumps(field_order)
    data.update(overrides)
    return data


SCHEMA_TWO_FIELDS = {
    "score": {"type": "int", "description": "Score"},
    "notes": {"type": "string", "description": "Notes"},
}

# jsonb stores this schema as ("notes", "score") — both names are 5 characters, so the
# bytewise tiebreak wins. Every expected order below is therefore ("score", "notes"),
# which is the one order that cannot be produced by falling back to schema order.


@pytest.mark.django_db()
def test_queue_form_persists_field_order(team):
    form = AnnotationQueueForm(data=_queue_form_data(SCHEMA_TWO_FIELDS, ["score", "notes"]))
    assert form.is_valid(), form.errors

    queue = form.save(commit=False)
    queue.team = team
    queue.created_by = team.members.first()
    queue.save()
    queue.refresh_from_db()

    assert queue.field_order == ["score", "notes"]
    assert queue.ordered_field_names() == ["score", "notes"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field_order", "expected_stored"),
    [
        pytest.param(None, [], id="absent-is-valid-and-stores-empty"),
        pytest.param([], [], id="empty-list-is-valid"),
    ],
)
def test_queue_form_without_field_order(team, field_order, expected_stored):
    form = AnnotationQueueForm(data=_queue_form_data(SCHEMA_TWO_FIELDS, field_order))
    assert form.is_valid(), form.errors

    queue = form.save(commit=False)
    queue.team = team
    queue.created_by = team.members.first()
    queue.save()
    queue.refresh_from_db()

    assert queue.field_order == expected_stored


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "field_order",
    [
        pytest.param(["score"], id="missing-a-schema-field"),
        pytest.param(["score", "notes", "ghost"], id="names-a-field-not-in-schema"),
    ],
)
def test_queue_form_rejects_field_order_not_matching_schema(team, field_order):
    form = AnnotationQueueForm(data=_queue_form_data(SCHEMA_TWO_FIELDS, field_order))

    assert not form.is_valid()
    assert "Field order must list exactly the schema's fields." in str(form.errors)


@pytest.mark.django_db()
def test_reorder_is_allowed_on_a_locked_queue(team):
    """The headline requirement of #4276: order stays editable after reviews start."""
    queue = AnnotationQueueFactory.create(team=team, schema=SCHEMA_TWO_FIELDS, field_order=["notes", "score"])
    item = AnnotationItemFactory.create(queue=queue, team=team)
    Annotation.objects.create(
        item=item,
        team=team,
        reviewer=team.members.first(),
        data={"score": 4, "notes": "ok"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 1

    form = AnnotationQueueForm(
        instance=queue,
        data=_queue_form_data(queue.schema, ["score", "notes"], name=queue.name),
    )
    assert form._schema_locked, "queue should be locked once a review exists"
    assert form.is_valid(), form.errors

    form.save()
    queue.refresh_from_db()
    assert queue.ordered_field_names() == ["score", "notes"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest apps/human_annotations/tests/test_forms.py -k "field_order or locked_queue" -v`

Expected: FAIL — `field_order` is not a form field, so it is ignored and never stored; the persistence and rejection assertions fail.

- [ ] **Step 3: Declare the form field**

In `apps/human_annotations/forms.py`, add the hidden field beside the existing `schema` declaration on `AnnotationQueueForm`:

```python
    field_order = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )
```

And add it to `Meta.fields`:

```python
        fields = ["name", "description", "schema", "field_order", "num_reviews_required"]
```

- [ ] **Step 4: Add the validation**

Add these two methods to `AnnotationQueueForm`, after `clean_schema` / `_validate_locked_schema_change`:

```python
def clean_field_order(self):
    raw = self.cleaned_data.get("field_order")
    if not raw:
        return []

    if isinstance(raw, list):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON: {e}") from e

    if not isinstance(data, list) or not all(isinstance(name, str) for name in data):
        raise ValidationError("Field order must be a list of field names")

    return data


def clean(self):
    cleaned = super().clean()
    schema = cleaned.get("schema")
    field_order = cleaned.get("field_order")
    # An absent field_order is valid: the resolver falls back to schema order. Only a
    # non-empty one is held to matching the schema exactly.
    if schema and field_order and set(field_order) != set(schema):
        raise ValidationError("Field order must list exactly the schema's fields.")
    return cleaned
```

Do **not** touch `_validate_locked_schema_change`. It compares `schema` only, so a re-order passes it untouched — that is the design, and the locked-queue test proves it.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_forms.py -v
```

Expected: PASS, including every pre-existing test in the file (they post without `field_order`, which stays valid).

- [ ] **Step 6: Lint, type check, commit**

```bash
uv run inv ruff --paths apps/human_annotations/forms.py apps/human_annotations/tests/test_forms.py
uv run inv typecheck --python --paths apps/human_annotations
git add apps/human_annotations/forms.py apps/human_annotations/tests/test_forms.py
git commit -m "$(cat <<'EOF'
Accept and validate field_order on the annotation queue form

A non-empty field_order must name exactly the schema's fields; an absent
one is valid and falls back to schema order. The schema lock is untouched,
so re-ordering a locked queue validates without a carve-out.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Order the server-rendered read surfaces

Points the prior-reviews panel and both export formats at the resolver. The reviewer's own form needs no change — it reads `get_field_definitions()`, which Task 1 already ordered.

**Files:**
- Modify: `apps/human_annotations/views/annotate_views.py:92`
- Modify: `apps/human_annotations/views/export_views.py:145` and `:156`
- Test: `apps/human_annotations/tests/test_views.py`

**Interfaces:**
- Consumes: `AnnotationQueue.ordered_field_names()` from Task 1.
- Produces: no new interfaces.

- [ ] **Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_views.py`. The `_pivot` helper already defined in that file (around line 798) hardcodes `list(queue.schema.keys())`; leave it alone and write these against the real entry points.

```python
@pytest.mark.django_db()
def test_export_csv_columns_follow_field_order(client, team_with_users):
    queue = AnnotationQueueFactory.create(
        team=team_with_users,
        schema={
            "score": {"type": "int", "description": "Score"},
            "notes": {"type": "string", "description": "Notes"},
        },
        field_order=["score", "notes"],
    )
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    user = team_with_users.members.first()
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"score": 5, "notes": "good"},
        status=AnnotationStatus.SUBMITTED,
    )
    client.force_login(user)

    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url)

    assert response.status_code == 200
    rows = list(csv.DictReader(response.content.decode().splitlines()))
    assert [row["field"] for row in rows] == ["score", "notes"]


@pytest.mark.django_db()
def test_prior_reviews_panel_follows_field_order(team_with_users):
    queue = AnnotationQueueFactory.create(
        team=team_with_users,
        schema={
            "score": {"type": "int", "description": "Score"},
            "notes": {"type": "string", "description": "Notes"},
        },
        field_order=["score", "notes"],
    )
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    user = team_with_users.members.first()
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"score": 5, "notes": "good"},
        status=AnnotationStatus.SUBMITTED,
    )

    context = _build_annotations_context(item, user, queue)

    assert [name for name, _ in context[0]["fields"]] == ["score", "notes"]
```

Add these imports to the top of `test_views.py` if not already present:

```python
import csv

from apps.human_annotations.views.annotate_views import _build_annotations_context
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest apps/human_annotations/tests/test_views.py -k "follow_field_order" -v`

Expected: FAIL — both assert `["score", "notes"]` but get `["notes", "score"]`. jsonb stores the two 5-character names bytewise, so `notes` sorts first; the order is still coming from the schema rather than `field_order`. Picking `["score", "notes"]` as the expected value is deliberate: it is the one ordering that cannot be produced by the schema-order fallback, so the test cannot pass by accident.

- [ ] **Step 3: Point the prior-reviews panel at the resolver**

In `apps/human_annotations/views/annotate_views.py`, inside `_build_annotations_context`, change line 92:

```python
    schema_fields = list(queue.schema.keys())
```

to:

```python
    schema_fields = queue.ordered_field_names()
```

- [ ] **Step 4: Point both exports at the resolver**

In `apps/human_annotations/views/export_views.py`, make the same change in **two** places — `_export_csv` (line 145) and `_export_jsonl` (line 156):

```python
        schema_fields = queue.ordered_field_names()
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_views.py -v
```

Expected: PASS, including the pre-existing export tests (their queues have no `field_order`, so they fall back to schema order and are unaffected).

- [ ] **Step 6: Lint, type check, commit**

```bash
uv run inv ruff --paths apps/human_annotations/views/annotate_views.py apps/human_annotations/views/export_views.py apps/human_annotations/tests/test_views.py
uv run inv typecheck --python --paths apps/human_annotations
git add apps/human_annotations/views/annotate_views.py apps/human_annotations/views/export_views.py apps/human_annotations/tests/test_views.py
git commit -m "$(cat <<'EOF'
Order the prior-reviews panel and CSV/JSONL exports by field_order

The reviewer's own form needs no change: it reads get_field_definitions(),
which is already ordered.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Order the three template surfaces that iterate their own dicts

The aggregates panel, the items-table summary column and the delete-confirm dialog each iterate a jsonb dict's own keys rather than the queue's order. The summary column's "first three fields" is currently three fields chosen by Postgres's key sort — this task is what makes that phrase mean something.

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py:175-181` (`AnnotationQueueDetail.get_context_data`)
- Modify: `templates/human_annotations/queue_detail.html:70`
- Modify: `templates/human_annotations/columns/annotations_summary.html:6`
- Modify: `templates/human_annotations/remove_session_confirm.html:29`
- Test: `apps/human_annotations/tests/test_views.py`

**Interfaces:**
- Consumes: `AnnotationQueue.ordered_field_names()` from Task 1.
- Produces: `AnnotationQueueDetail` context key `aggregates` changes type from `dict[str, dict]` to `list[tuple[str, dict]]`. No other consumer of that key exists.

- [ ] **Step 1: Write the failing test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_aggregates_panel_follows_field_order(client, team_with_users):
    queue = AnnotationQueueFactory.create(
        team=team_with_users,
        schema={
            "score": {"type": "int", "description": "Score"},
            "rating": {"type": "int", "description": "Rating"},
        },
        field_order=["rating", "score"],
    )
    AnnotationQueueAggregateFactory.create(
        team=team_with_users,
        queue=queue,
        aggregates={
            "rating": {"type": "numeric", "count": 1, "mean": 2},
            "score": {"type": "numeric", "count": 1, "mean": 5},
        },
    )
    client.force_login(team_with_users.members.first())

    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)

    assert response.status_code == 200
    # jsonb sorts by length first, so schema order is ("score", "rating"); asserting the
    # reverse is what proves field_order won.
    assert [name for name, _ in response.context["aggregates"]] == ["rating", "score"]
```

Add the factory import to `test_views.py` if not already present:

```python
from apps.utils.factories.human_annotations import AnnotationQueueAggregateFactory
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest apps/human_annotations/tests/test_views.py -k "aggregates_panel_follows" -v`

Expected: FAIL — `aggregates` is a dict, so unpacking `for name, _ in ...` iterates keys and raises `ValueError: too many values to unpack`, or yields the jsonb order.

- [ ] **Step 3: Build ordered pairs in the view**

In `apps/human_annotations/views/queue_views.py`, replace the `context["aggregates"]` block in `AnnotationQueueDetail.get_context_data` (currently lines 175-181):

```python
        aggregate = getattr(queue, "aggregate", None)
        schema = queue.schema or {}
        stored = aggregate.aggregates if aggregate else {}
        context["aggregates"] = [
            (name, merge_binary_labels(stored[name], schema.get(name) or {}))
            for name in queue.ordered_field_names()
            if name in stored
        ]
```

Iterating `ordered_field_names()` and filtering on presence also drops aggregate entries for fields the schema no longer names — those have no label context and previously rendered as bare rows.

- [ ] **Step 4: Render the pairs in the detail template**

In `templates/human_annotations/queue_detail.html`, change line 70 from:

```django
        {% for field_name, stats in aggregates.items %}
```

to:

```django
        {% for field_name, stats in aggregates %}
```

Nothing else in that block changes — `{% if aggregates %}` is still correct for a list.

- [ ] **Step 5: Order the items-table summary column**

Replace the whole of `templates/human_annotations/columns/annotations_summary.html` with:

```django
{% load default_tags %}
{% with annotations=record.submitted_annotations %}
  {% if annotations %}
    {% for ann in annotations|slice:":3" %}
      <div class="text-xs">
        {% if ann.is_authoritative %}<i class="fa-solid fa-circle-check text-primary" title="Authoritative"></i> {% endif %}<span class="font-medium">{{ ann.reviewer.get_full_name|default:ann.reviewer.username }}</span>:
        {% for key in record.queue.ordered_field_names|slice:":3" %}{% if not forloop.first %}, {% endif %}{{ key }}: {{ ann.data|get_item:key|default_if_none:"" }}{% endfor %}
      </div>
    {% endfor %}
    {% if annotations|length > 3 %}
      <div class="text-xs text-gray-400">+{{ annotations|length|add:"-3" }} more</div>
    {% endif %}
  {% else %}
    <span class="text-gray-400 text-xs">No annotations</span>
  {% endif %}
{% endwith %}
```

Two things matter here:

- `default_if_none`, **not** `default`. A binary field's stored value is the integer `0`, which is falsy — `default` would blank it out and show a harmless-looking empty cell for "No".
- `record.queue` is already covered by `select_related("session__experiment", "message", "queue")` in `AnnotationQueueItemsTableView.get_queryset` (`queue_views.py:207`), so this adds no per-row query.

- [ ] **Step 6: Order the delete-confirm dialog**

In `templates/human_annotations/remove_session_confirm.html`, add the load tag at the top, beside the existing `{% load i18n %}`:

```django
{% load i18n default_tags %}
```

Then replace the inner loop at line 29:

```django
          {% for key, value in ann.data.items %}
            <span class="badge badge-sm badge-outline">{{ key }}: {{ value }}</span>
          {% endfor %}
```

with:

```django
          {% for key in queue.ordered_field_names %}
            <span class="badge badge-sm badge-outline">{{ key }}: {{ ann.data|get_item:key|default_if_none:"" }}</span>
          {% endfor %}
```

`queue` is already in this template's context (`queue_views.py:478`).

The spec suggests extracting the `(name, value)` pair builder out of
`_build_annotations_context` and reusing it here. Don't — the `get_item` filter already
does the job from the template, so there is no duplicated logic to extract and the view
keeps its current shape. Note the divergence if you are reading the spec alongside this.

- [ ] **Step 7: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_views.py -v
```

Expected: PASS. If a pre-existing test asserts on `context["aggregates"]` as a dict, update it to the list-of-pairs shape — that type change is intended and documented in this task's Interfaces block.

- [ ] **Step 8: Lint, type check, commit**

```bash
uv run inv ruff --paths apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py
uv run inv typecheck --python --paths apps/human_annotations
git add apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py templates/human_annotations/
git commit -m "$(cat <<'EOF'
Order the aggregates panel, summary column and delete-confirm dialog

Each iterated a jsonb dict's own keys. The summary column's "first three
fields" now means the author's first three rather than the three Postgres
happened to sort first.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Re-order controls in the schema builder

Adds the grip and ▲▼ buttons to each field card and submits the resulting order. This is inline Alpine in a Django template with no JS unit-test harness, so it is verified by driving the running app.

**Files:**
- Modify: `templates/human_annotations/queue_form.html` — locked notice (line 28), card header (lines 35-52), add-field button area (~line 198), hidden inputs (~line 208), Alpine component (lines 249-350)

**Interfaces:**
- Consumes: the `field_order` form field from Task 2.
- Produces: no Python interfaces. Submits `field_order` as a JSON array of trimmed field names, filtered by the same rule `schemaJson` uses.

- [ ] **Step 1: Update the locked notice**

At `queue_form.html:28`, replace the notice text so it states what stays editable:

```django
          <span>{% translate "Schema structure is locked after annotations have started. You can still re-order fields and change whether they are required." %}</span>
```

- [ ] **Step 2: Add the re-order controls to the card header**

In the `x-for` card body, replace the "Field Name" block (lines 35-52) with:

```django
            <div class="mb-2">
              <div class="flex gap-2 items-center mb-1"
                   :class="locked && 'bg-base-100 rounded-lg p-2 border border-primary/30'">
                <span class="cursor-grab text-base-content/50 px-1"
                      draggable="true"
                      @dragstart="onDragStart(index, $event)"
                      @dragover.prevent
                      @drop.prevent="onDrop(index)"
                      @dragend="dragIndex = null"
                      title="{% translate "Drag to re-order" %}">
                  <i class="fa fa-grip-vertical"></i>
                </span>
                <span class="label-text text-sm flex-1" :class="locked && 'font-semibold'">
                  <span x-show="!locked">{% translate "Field Name" %}</span>
                  <span x-show="locked">{% translate "Field order" %}</span>
                </span>
                <button type="button"
                        @click="moveField(index, -1)"
                        :disabled="index === 0"
                        class="btn btn-outline btn-xs"
                        title="{% translate "Move up" %}">
                  <i class="fa fa-arrow-up"></i>
                </button>
                <button type="button"
                        @click="moveField(index, 1)"
                        :disabled="index === fields.length - 1"
                        class="btn btn-outline btn-xs"
                        title="{% translate "Move down" %}">
                  <i class="fa fa-arrow-down"></i>
                </button>
                <button type="button"
                        @click="removeField(index)"
                        x-show="!locked"
                        class="btn btn-outline btn-xs btn-error">
                  <i class="fa fa-trash"></i>
                </button>
              </div>
              <input type="text"
                     x-model="field.name"
                     placeholder="{% translate "e.g., 'accuracy', 'score'" %}"
                     class="input input-bordered w-full input-sm"
                     :disabled="locked"
                     required>
            </div>
```

The grip and the two arrow buttons are deliberately **not** bound to `locked`. Only the delete button keeps `x-show="!locked"`.

- [ ] **Step 3: Add the hidden input**

Next to the existing hidden schema input (~line 208), add:

```django
    <input type="hidden" name="field_order" :value="fieldOrderJson">
```

- [ ] **Step 4: Add the component state and methods**

In `Alpine.data('schemaFieldBuilder', ...)`, add `dragIndex` to the returned state object alongside `locked` and `fields`:

```javascript
        dragIndex: null,
```

Then add these three methods next to `removeField`:

```javascript
        moveField(index, delta) {
          const target = index + delta;
          if (target < 0 || target >= this.fields.length) {
            return;
          }
          const [moved] = this.fields.splice(index, 1);
          this.fields.splice(target, 0, moved);
        },

        onDragStart(index, event) {
          this.dragIndex = index;
          event.dataTransfer.effectAllowed = 'move';
        },

        onDrop(index) {
          if (this.dragIndex === null || this.dragIndex === index) {
            return;
          }
          const [moved] = this.fields.splice(this.dragIndex, 1);
          this.fields.splice(index, 0, moved);
          this.dragIndex = null;
        },
```

- [ ] **Step 5: Add the `fieldOrderJson` getter**

Add directly after the existing `get schemaJson()` getter:

```javascript
        get fieldOrderJson() {
          return JSON.stringify(
            this.fields
              .filter(field => field.name && field.name.trim())
              .map(field => field.name.trim())
          );
        },
```

The `filter` must stay identical to the `if (field.name && field.name.trim())` guard inside `schemaJson` (line 314). If they diverge, a half-typed new field makes `field_order` and `schema` disagree and the form rejects the save with "Field order must list exactly the schema's fields."

- [ ] **Step 6: Build assets and lint the template**

```bash
pnpm run dev
uv run inv ruff --paths templates/human_annotations/queue_form.html
```

`ruff` will skip the template; the pre-commit `djLint` and `Djade` hooks are what check it, so run `git add` plus `pre-commit run --files templates/human_annotations/queue_form.html` if you want that feedback before committing.

- [ ] **Step 7: Verify in the running app**

Per AGENTS.md "Verifying changes", tests are necessary but not sufficient here — this step is the real gate for a UI change.

```bash
uv run inv runserver
```

If the dev database has no annotation queues, create one:

```bash
uv run python manage.py shell -c "
from apps.human_annotations.models import AnnotationQueue
from apps.teams.models import Team, Flag
team = Team.objects.first()
flag, _ = Flag.objects.get_or_create(name='flag_human_annotations')
flag.teams.add(team)
AnnotationQueue.objects.create(
    team=team, name='Reorder check', created_by=team.members.first(),
    schema={
        'overall_quality': {'type': 'int', 'description': 'Quality', 'ge': 1, 'le': 5},
        'tone': {'type': 'choice', 'description': 'Tone', 'choices': ['warm', 'curt']},
        'notes': {'type': 'string', 'description': 'Notes'},
    },
)
"
```

Then, at `/a/<team-slug>/human-annotations/queue/`:

- [ ] Open a queue for editing. Confirm the cards render in schema order and each has a grip and ▲▼.
- [ ] ▲ is disabled on the first card, ▼ on the last.
- [ ] Type a few characters into a field's Description, then move that card with ▲. Confirm the typed text survives the move — this is the `:key="field._id"` behaviour and is the check that the `x-for` keying still holds.
- [ ] Drag a card by its grip to a new position.
- [ ] Save, reload the edit page, and confirm the new order persisted.
- [ ] Open the queue's annotate page and confirm the reviewer's form shows the fields in that order.
- [ ] Download the CSV export and confirm the `field` rows follow the same order.
- [ ] On a queue that already has a submitted review: confirm the name/type/constraint inputs are disabled, the delete buttons are gone, the grip and ▲▼ are still usable, the re-order row and the Required box both carry the primary border, and saving a re-order succeeds.

- [ ] **Step 8: Commit**

```bash
git add templates/human_annotations/queue_form.html
git commit -m "$(cat <<'EOF'
Add drag and move up/down controls to the schema field builder

Grip plus arrow buttons on each field card; the arrows carry the
keyboard-accessible path, which is why the hand-rolled HTML5 drag does not
have to. Both stay live on a locked queue and take the primary-border
promotion the Required box already uses.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Record the decision as an ADR

ADR-0015 says only `required` stays mutable once a queue locks. That is now untrue, and the reasoning for the exemption belongs somewhere citable.

**Files:**
- Create: `docs/adr/0068-field-order-is-presentation-outside-the-locked-schema.md`
- Modify: `docs/adr/index.md`

**Interfaces:** none.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0068-field-order-is-presentation-outside-the-locked-schema.md`. Follow ADR-0055's structure exactly — it is the existing example of an ADR that extends ADR-0015 rather than editing it:

```markdown
# ADR-0068: Field order is presentation, stored outside the locked schema

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
```

- [ ] **Step 2: Add the index row**

Append to the table at the end of `docs/adr/index.md`, matching the existing row format exactly:

```markdown
| [0068](0068-field-order-is-presentation-outside-the-locked-schema.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Field order is presentation, stored outside the locked schema |
```

- [ ] **Step 3: Verify the links resolve**

```bash
ls docs/adr/0068-field-order-is-presentation-outside-the-locked-schema.md
grep -c "0068" docs/adr/index.md
```

Expected: the file exists and the index has exactly one `0068` row.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0068-field-order-is-presentation-outside-the-locked-schema.md docs/adr/index.md
git commit -m "$(cat <<'EOF'
Add ADR-0067: field order is presentation, outside the locked schema

Extends ADR-0015, which said only `required` stays mutable once a queue
locks.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] `uv run pytest apps/human_annotations -v` — whole app green.
- [ ] `uv run pytest apps/evaluations -v` — confirms nothing leaked into the shared `FieldDefinition` consumers.
- [ ] `uv run inv typecheck` — both Python and TS.
- [ ] `git log --oneline main..HEAD` — six commits, one per task.
- [ ] The PR description uses `.github/pull_request_template.md`. Tick **"The migrations are backwards compatible"** — `field_order` is nullable and added with no data migration, so the previous release keeps serving through the deploy. Leave the docs/changelog box for the author to judge; the ADR lives in this repo but the public changelog does not.
