# Tag Removal on Evaluation Rerun

**Date:** 2026-05-22
**Issue:** [#3409](https://github.com/dimagi/open-chat-studio/issues/3409)

## Summary

When an evaluation is rerun (full or delta), tags that were previously applied by the evaluator but no longer warranted by the new results should be removed. Tags outside the evaluator's managed set are never touched.

## Background

`AppliedTag` is an audit record linking an `EvaluationResult` to the `EvaluatorTagRule` and `Tag` that produced it. The actual tag presence on a `Chat` or `ChatMessage` is stored in a `CustomTaggedItem` (Django taggit generic FK). These are two separate concerns: `AppliedTag` records who applied a tag; `CustomTaggedItem` records that the tag is present.

When a new run is created, fresh `EvaluationResult`s and `AppliedTag`s are written, but old `CustomTaggedItem`s are never cleaned up. This means stale tags accumulate across reruns.

## Design Philosophy

Tags are a team-wide signal: a message or session either meets the criteria for a tag or it does not. Evaluators act as reviewers — their ruleset defines the universe of tags they manage, and each run is authoritative for that universe.

**Important:** This means a tag applied manually by a user can be removed if it falls within an evaluator's managed set and the evaluator decides not to apply it. This is intentional — a single tag carries the same meaning regardless of how it was applied, and the evaluator's verdict takes precedence.

Similarly, if two evaluators both manage the same tag, a rerun of one evaluator may remove a tag that the other evaluator also applied. This is acceptable under the same philosophy: the tag represents a shared team-wide criterion, and the running evaluator's result is treated as authoritative.

## Architecture

All logic lives in `apps/evaluations/tagging.py`, alongside the existing `apply_rules_to_result`. A new `reverse_stale_tags(run)` function is called at the end of `mark_evaluation_complete` in `apps/evaluations/tasks.py`. No new Celery tasks and no schema changes are required.

PREVIEW runs skip cleanup entirely, consistent with the existing behaviour that skips tag application for previews.

## Algorithm

`possible_tags` is computed once per run (shared across all messages):

```
possible_tags = all Tag IDs referenced by EvaluatorTagRules
                across all evaluators attached to run.config
```

For each `EvaluationMessage` evaluated in the run:

1. **Resolve the target** — call `resolve_target(evaluation_message)` to get the `Chat` or `ChatMessage` object. If it returns `None` (e.g. CSV-imported messages with no linked chat), skip — consistent with existing `apply_rules_to_result` behaviour.

2. **Compute `applied_tags`** — tag IDs in `AppliedTag` records for the current run scoped to this message:
   ```
   AppliedTag.objects.filter(
       evaluation_result__run=run,
       evaluation_result__message=message,
   ).values_list('tag_id', flat=True)
   ```

3. **Compute `stale_tags`** — `possible_tags − applied_tags`.

4. **Delete stale `CustomTaggedItem`s** — bulk-delete where `(content_type, object_id, tag_id)` matches the resolved target object and any tag in `stale_tags`.

For delta runs, the messages iterated are the scoped subset already, so cleanup is naturally limited to those messages.

## Edge Cases

| Scenario | Behaviour |
|---|---|
| Rule removed between runs | Removed tag falls out of `possible_tags`; future reruns will not clean it up. Tag persists on affected messages until manually removed. This is intentional — removing a rule means the evaluator no longer manages that tag. |
| Rule added between runs | New tag enters `possible_tags`. If message does not meet criteria, it lands in `stale_tags`, but no `CustomTaggedItem` exists to delete — no-op. |
| First run (no prior tags) | `stale_tags` = `possible_tags` for messages where nothing was applied, but all deletes are no-ops since no `CustomTaggedItem`s exist yet. |
| `resolve_target` returns `None` | Message is skipped; no cleanup attempted. |
| PREVIEW run | Cleanup skipped entirely. |

## Testing

Tests in `apps/evaluations/tests/`:

- Tags in `stale_tags` are removed from the target object.
- Tags in `applied_tags` are kept on the target object.
- Tags outside `possible_tags` (not managed by the evaluator) are untouched.
- `resolve_target` returning `None` is handled gracefully (no error, no cleanup).
- Delta run only cleans up messages in the scoped subset; other messages are unaffected.
- PREVIEW run skips cleanup entirely.

Existing tests in `apps/annotations/tests/test_tags.py` require no changes.
