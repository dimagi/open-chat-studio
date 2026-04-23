# Plan: Eval-Driven Tagging (#3193)

## Goal
Let evaluators tag sessions / chat messages based on their output, idempotently, without clobbering human-applied tags. Tagging target is deduced from the evaluator's `evaluation_mode` (SESSION → the ExperimentSession; MESSAGE → the `expected_output_chat_message`).

## Models

### Modified: `annotations.TagCategories`
- Add `EVALUATIONS = "evaluations", _("Evaluations")`.

### New: `evaluations.EvaluatorTagRule(BaseTeamModel)`
- `evaluator` FK → `Evaluator` (CASCADE, `related_name="tag_rules"`)
- `tag` FK → `annotations.Tag` (PROTECT; always `category=EVALUATIONS`, `is_system_tag=True`, same team as evaluator)
- `field_name` str — must exist in `evaluator.params["output_schema"]`
- `condition_type` choice: `equals` (choice fields) | `range` (numeric fields)
- `condition_value` JSON object with strict keys per type:
  - `equals`: `{"value": ...}` (exactly this key)
  - `range`: `{"min": x, "max": y}` (both inclusive, exactly these keys)
  - Extra keys fail validation loudly.
- No `target` field — derived from `evaluator.evaluation_mode` at runtime.
- `clean()` delegates to pure validator helpers:
  - `_validate_field_in_schema(field_name, output_schema) -> FieldDefinition`
  - `_validate_condition_matches_field(condition_type, condition_value, field_definition)` — reused as `validate_condition()` helper callable from forms, scripts, and the evaluator-edit cascade validator
  - `_validate_tag_compatibility(tag, evaluator)` — category, team, is_system_tag checks
- `team` enforced equal to `evaluator.team` in `clean()`.

### New: `evaluations.AppliedTag(BaseTeamModel)`
Audit row recording which rule applied which tag on which evaluation result. This is an eval-specific model, **not** a through-model for `EvaluationResult.applied_tags` — writes are explicit `bulk_create` calls, not implicit M2M operations.

```python
class AppliedTag(BaseTeamModel):
    evaluation_result = FK(EvaluationResult, on_delete=CASCADE, related_name="applied_tags")
    rule = FK(EvaluatorTagRule, on_delete=CASCADE, related_name="applications")
    tag = FK("annotations.Tag", on_delete=PROTECT)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["evaluation_result", "rule", "tag"],
                name="unique_applied_tag_per_result_rule",
            ),
        ]
        indexes = [
            Index(fields=["rule"]),  # powers rule-cleanup queries
        ]
```

Target identity is derived from `evaluation_result.message` + `rule.evaluator.evaluation_mode` — no redundant FK storage.

### Modified: `evaluations.EvaluationResult`
- `related_name="applied_tags"` on the reverse accessor from `AppliedTag.evaluation_result`. `result.applied_tags.all()` returns `AppliedTag` rows (audit), not `Tag` rows.

### Unchanged: `annotations.CustomTaggedItem`
No new fields. Safe cleanup is driven by `tag.category=EVALUATIONS` + the `AppliedTag` audit trail.

## Target resolution

Derived deterministically from the evaluator's mode:

```python
def resolve_target(evaluator, evaluation_message):
    if evaluator.evaluation_mode == EvaluationMode.SESSION:
        return evaluation_message.session  # may be None → rule no-ops
    return evaluation_message.expected_output_chat_message  # may be None → rule no-ops
```

Null targets (CSV import, manual message) are silent no-ops for that rule.

## Code organization

### New module: `apps/evaluations/tagging.py`

Separates pure predicate logic from DB side-effects so the bulk of the feature is unit-testable without `@pytest.mark.django_db`.

**Pure functions (no DB):**
- `validate_condition(condition_type, condition_value, field_definition) -> None` — raises on invalid shape.
- `matches(condition_type, condition_value, field_value) -> bool` — flat `match/case` dispatch over `ConditionType.EQUALS | ConditionType.RANGE`.
- `evaluate_rules(rules, result_output) -> list[EvaluatorTagRule]` — returns rules that match; skips rules whose `field_name` is absent from `result_output["result"]` or whose types don't match, logging a warning (defensive runtime handling per Issue 9).
- `resolve_target(evaluator, evaluation_message) -> Session | ChatMessage | None`.

**DB-touching function:**
- `apply_rules_to_result(evaluation_result, evaluator, evaluation_message) -> None` — orchestrates the 6 steps below.

## Evaluation flow (`evaluate_single_message_task`)

Top-level change: prefetch rules+tags once per task to avoid per-message N+1.

```python
evaluators_qs = Evaluator.objects.filter(id__in=evaluator_ids).prefetch_related(
    Prefetch("tag_rules", queryset=EvaluatorTagRule.objects.select_related("tag")),
)
evaluators = {e.id: e for e in evaluators_qs}
```

Per message + evaluator:

1. **Skip tagging** if `result.output` has an error, or `evaluation_run.type == PREVIEW`.
2. **Enumerate** prefetched `evaluator.tag_rules.all()`.
3. **Evaluate** each rule against `result.output["result"][field_name]` via `matches()`. Rules referencing missing/type-mismatched fields log + skip (schema drift defense).
4. **Resolve target** via `resolve_target(evaluator, message)`. If None, all rules no-op silently for this result.
5. **Remove** per target: `{tags from all rules for this evaluator} − {tags to apply now}`. Query:
   ```python
   CustomTaggedItem.objects.filter(
       content_type=ContentType.objects.get_for_model(type(target)),
       object_id=target.pk,
       tag__in=to_remove,
       tag__category=TagCategories.EVALUATIONS,  # defense-in-depth safety filter
   ).delete()
   ```
6. **Apply** per `(target, tag)`: `target.tags.add(tag, through_defaults={"team": team, "user": None})`. Idempotent via the `CustomTaggedItem` unique constraint.
7. **Record** audit rows in a single `AppliedTag.objects.bulk_create([...])` call — one row per matched rule.

Steps 2–7 + `EvaluationResult.objects.create(...)` run inside a single `transaction.atomic()` block **per evaluator** inside the loop. Failure rolls back the whole tagging side-effect along with the result row; other evaluators' results on the same message still succeed independently.

Removals are driven by the *current* rule set, not the prior run's `AppliedTag` history. Rule deletion without the cleanup prompt leaves historical tags in place (see "Deferred: rule delete / update UX" under "Out of scope" below).

### Current behavior around rule delete/update

Today rule deletion cascades the `AppliedTag` audit trail but leaves `CustomTaggedItem` rows on their targets. Cleanup of orphan tags is tracked as a follow-up. The per-run removal semantics in `apply_rules_to_result` still work — deleting a rule just means its target tags won't be cleared by subsequent runs because the rule no longer exists to drive that cleanup.

The `AppliedTag` audit table and its `rule` index are retained so a future follow-up can drive cleanup queries from the audit trail without a schema change.

## Category isolation

Because `Tag.unique_together = ("team", "name", "is_system_tag", "category")`, an evaluations-category tag `"foo"` is a distinct row from a user-defined `"foo"`. Evaluator-applied rows and human-applied rows with the same name literally reference different `Tag` FKs and cannot collide. Removal filters on `tag__category=EVALUATIONS` and cannot touch human tags or other system categories (`BOT_RESPONSE`, etc.).

Known UX debt: sessions tagged with both user and eval `"foo"` display two badges. A category-aware badge UI (distinguishing user vs. eval origin visually) is tracked as a follow-up — not blocking for this feature.

## Conditions

- `equals`: field must be `ChoiceFieldDefinition`; `condition_value["value"]` must be in `field.choices`.
- `range`: field must be `IntFieldDefinition` | `FloatFieldDefinition`; inclusive both bounds; values coerced to the field's `python_type`; `min ≤ max` enforced.

All validation lives in the pure `validate_condition()` helper; `EvaluatorTagRule.clean()` calls it.

## Output schema drift

The evaluator edit form validates all dependent rules against the new `output_schema` before saving:

- In `EvaluatorForm.clean()` (or save hook), after `params["output_schema"]` is parsed, iterate `self.instance.tag_rules.all()` and re-run each rule's `validate_condition()`.
- If any rule fails, block the save and surface the offending rule's `field_name` in the error message.

Runtime is defensive too: `evaluate_rules()` skips rules whose `field_name` is absent or type-mismatched from `result_output`, logging a warning. Belt and suspenders guard against drift from management commands, data migrations, or future API paths.

## UI

On the evaluator edit page (`EvaluatorForm`), add a repeatable "Tag rules" section below the output schema editor. Each row:
- Tag name (autocomplete on existing team `EVALUATIONS`-category tags; creates `Tag` with `category=EVALUATIONS, is_system_tag=True` if new)
- Field dropdown (populated from `output_schema` keys)
- Condition dropdown (options filtered by selected field's type)
- Value inputs (single value for `equals`; min/max for `range`)

**No target dropdown** — target is implied by the evaluator's `evaluation_mode`.

Implemented as a Django formset on `EvaluatorTagRule`. Rules can be added, edited, and removed inline via the trash-icon on each row. Deletion does not prompt today; any tags previously applied by a deleted rule remain on their targets until a future follow-up wires up the cleanup prompt (see "Out of scope" below).

## Migrations

1. Add `EVALUATIONS` to `TagCategories` (code-only; `TextChoices` doesn't need a DB migration).
2. Create `EvaluatorTagRule` table (`BaseTeamModel`).
3. Create `AppliedTag` table (`BaseTeamModel`, index on `rule`).
4. No data migration (new feature).

## Testing

### Pure unit tests (`apps/evaluations/tests/test_tagging_logic.py`, no `@pytest.mark.django_db`)
- `matches()` — equals match/non-match; range at-min, at-max, below, above; float coercion; unknown condition type raises.
- `validate_condition()` — valid choice, invalid choice, missing choices list, wrong field type, range min > max, non-numeric field, missing/extra keys in JSON.
- `evaluate_rules()` — empty list, mixed matches, missing field (no-op + warn), type mismatch (no-op + warn).
- `resolve_target()` — SESSION with/without session; MESSAGE with/without `expected_output_chat_message`.

### DB integration tests (`apps/evaluations/tests/test_tagging_integration.py`, `@pytest.mark.django_db`)
- `EvaluatorTagRule.clean()` — wrong category, cross-team tag, wrong field type → `ValidationError`.
- `apply_rules_to_result()` happy path — `CustomTaggedItem` + `AppliedTag` created.
- Idempotency — run twice → one `CustomTaggedItem`, two `AppliedTag`.
- Removal semantics — update rule condition between runs, old tag removed.
- Category isolation — user + eval `"foo"` coexist; cleanup only removes eval.
- Skip on `PREVIEW` run and on `output["error"]`.
- Null target no-op (CSV-imported `EvaluationMessage`).
- Concurrent evaluators on same message — both tags land.
- Transaction rollback — mock `bulk_create` raising; `EvaluationResult` + tags both roll back.

### E2E wiring test
One `evaluate_single_message_task` test with mocked `Evaluator.run()` returning canned output; asserts tag lands on target, `AppliedTag` recorded, `EvaluationResult` created.

### Factories (`apps/utils/factories/evaluations.py`)
- `EvaluatorTagRuleFactory` — subfactory for evaluator + eval-category tag; sensible condition defaults.
- `AppliedTagFactory` — for setting up "rule has existing applications" scenarios.
- Enrich `EvaluatorFactory.params["output_schema"]` with one `ChoiceFieldDefinition` + one `IntFieldDefinition` to support condition tests.

## Out of scope

- Tagging the *generated* session from `run_bot_generation` (explicitly rejected).
- Multi-condition rules (deferred; see note below).
- Category-aware badge UI for user↔eval tag name collisions (tracked as follow-up).
- "Clean up orphaned eval tags" management command (add if orphaning from non-prompted rule deletions becomes a complaint).

### Deferred: rule delete / update UX (per-rule, not per-evaluator)

The sections below are a brief for the follow-up PR. They are **not** implemented in this feature. There is **no** evaluator-level cleanup prompt. Evaluator delete cascades rules + `AppliedTag` rows away but does not touch `CustomTaggedItem` rows. The `AppliedTag` audit trail is retained specifically so this follow-up can drive the cleanup queries.

#### On rule delete
- Count `CustomTaggedItem` rows tied to this rule via its `AppliedTag` history:
  ```python
  applications = rule.applications.select_related(
      "evaluation_result__message__session",
      "evaluation_result__message__expected_output_chat_message",
  )
  ```
- For each application, resolve target via `_target_for(applied_tag)` (same logic as `resolve_target`, reading from the audit row's result→message).
- Query `CustomTaggedItem.objects.filter(tag=rule.tag, ...)` per target to compute the cleanup count.
- Show a confirmation: "Also remove N tags this rule applied from their targets?"
- If checked: delete those `CustomTaggedItem` rows in the same transaction as the rule delete.
- `AppliedTag` rows cascade away with the rule regardless.

#### On rule update
Behavioral fields: `tag`, `field_name`, `condition_type`, `condition_value`.

- `EvaluatorTagRuleForm` inspects `form.changed_data` against `BEHAVIORAL_FIELDS = {"tag", "field_name", "condition_type", "condition_value"}`.
- If any behavioral field changed AND `rule.applications.exists()`: render an intermediate confirmation step.
- User can choose to clean up existing `CustomTaggedItem` rows (same query shape as delete) before the save lands, or skip cleanup.
- Save proceeds in either case.

`evaluator.evaluation_mode` is immutable after creation (enforced at `forms.py:720-722`), so mode changes cannot invalidate existing rules.

## Considered: multi-condition rules

Per-rule array of conditions with OR semantics — deferred. Trade-offs unchanged from original analysis:

1. **Validation surface** — per-condition `field_name` + type + value checks run N× per rule; error reporting inside a JSON array is awkward in form UIs.
2. **UI complexity** — nested formset roughly doubles frontend code vs. the flat single-condition formset.
3. **Audit trail fidelity** — with single-condition rules, the rule row *is* the audit record. OR-conditions require either a through-column on `AppliedTag` recording the matched condition index, or accepting that edits mutate past meaning.

Workaround today: users wanting "one tag, many triggers" create multiple rules with the same tag. The shared `Tag` row plus idempotent apply plus unique `CustomTaggedItem` means sessions show one badge per tag regardless of trigger count.

Future migration path is additive: rule gains a `conditions` JSON array; old single-condition fields become its first entry.

## Note on tag identity

`Tag` rows are unique on `(team, name, is_system_tag, category)`. Multiple rules pointing at tag name `"unacceptable"` all FK to the same `Tag` row; applying it to a target multiple times in one run is a no-op via the `CustomTaggedItem` unique constraint. Sessions display one badge per tag, never duplicates, regardless of how many rules triggered it. Downstream filter UIs (e.g. future dataset auto-import by tag) see exactly one entry per `(category, name)` and should disambiguate cross-category collisions (human `"unacceptable"` vs eval `"unacceptable"`) via category-aware selectors with badges, not by merging rows.
