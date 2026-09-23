# Per-chatbot team sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `manage.py sync_team` move a team-admin-selected set of chatbots (and only the team-level resources those chatbots reach) instead of the whole team.

**Architecture:** The selection lives on the source server as `Team.exportable_experiments`, an M2M to `Experiment`; empty means "export everything". The export API reads it off `request.team`, builds a `ChatbotScope` once per request, and applies it as a second filter dimension on top of the existing team-scoped queryset. The sync client sends nothing — it learns the selection from `GET /api/export/team/`, uses it to skip the resource classes the source will serve empty, and keys its pagination cursors by it.

**Tech Stack:** Django 5.2 / Python 3.13, PostgreSQL (pgvector), DRF + drf-spectacular, Celery, SQLite for client-side sync state, HTMX + Alpine.js + TomSelect for the settings UI, pytest.

**Spec:** `docs/design/per-chatbot-sync.md`

## Global Constraints

- PostgreSQL only. Postgres-only ORM features and `django.contrib.postgres.operations` are fine.
- Every migration must run correctly against both the old and the new code: the deploy applies migrations to completion while the previous release is still serving. No `NOT NULL` columns without a DB-level default.
- Index migrations use `AddIndexConcurrently` with `atomic = False` (existing pattern: `apps/chat/migrations/0025_chatmessage_chatmessage_created_at_idx.py`).
- Catch database exceptions *outside* `transaction.atomic()`, or inside a nested `atomic()` savepoint. Enforced by the `atomic-exception-handling` pre-commit hook.
- Tests must not depend on migration-seeded global rows (`LlmProviderModel`, `PricingRule`) — the suite runs with `--reuse-db`.
- `api-schemas/export.yml` is generated; CI regenerates it. Never hand-edit it. Any change to the export serializers or the manifest payload changes `schema_checksum()`, so both servers need the new code — the existing preflight already enforces this.
- Lint and format every touched Python file with `uv run inv ruff --paths <path>`; type check with `uv run inv typecheck --paths <path>`.
- One change type per commit (`feat`, `fix`, `refactor`, `test`, `chore`, `docs`). Do not mix.
- Do not run `makemigrations`/`migrate` against anything but a local dev DB.
- Comments describe the code as it stands. No diff narration, no pre-rebutting alternatives nobody proposed.

## Deviations from the spec (decided while writing this plan, apply as written)

1. **`(updated_at, id)` indexes go on 10 models, not all 23.** The spec says "the in-scope models". The 10 are exactly the class (a)/(b) models that page by `updated_at_id`; the 13 left out are all class (c) (`evaluations.*`, `human_annotations.*`, `analysis.*`) or small enough that a top-N sort is free. Listed in Task 1.
2. **A standing export bug was found while scoping this work and is fixed in Task 3.** `TEAM_PATH_REGISTRY["events.eventaction"]` covers `static_trigger` and `timeout_trigger` but not `scheduled_trigger`, so a scheduled trigger's `EventAction` is never exported and importing the trigger raises `UnresolvedForeignKey` on its non-null `action` FK. Verified against this branch with a throwaway test. It blocks the class (a) rule for `events.eventaction`, so it lands first.
3. **`bot_channels.experimentchannel` is scoped as `Q(experiment_id__in=family) | Q(experiment__isnull=True)`** rather than the spec's "team web/API channels referenced by synced sessions". Team-level channels are the ones with `experiment IS NULL`, there are a handful per team, and this avoids a `DISTINCT` over the session table on every page. A session pointing at *another* chatbot's channel (the `session.is_stale()` case) loses the link — the FK is nullable, so it nulls rather than failing.
4. **`experiments.participant` unions three semi-joins, not one.** `ParticipantData.participant` and `ScheduledMessage.participant` are both non-null and both are scoped by experiment, so a participant reachable only through those must be exported or `resolve_fk` raises.
5. **Cursor reset is "reset unless the new selection is a subset of a previously synced one."** The spec's "reset only when the selection grows" is implemented as: cursors are keyed by selection, and a new selection seeds its cursors from any stored selection that is a superset of it. Shrinking costs nothing; growing costs a re-read (not a re-import, because of Task 6).
6. **`frozen_experiment_q()` is not cached.** The spec calls a short cache "reasonable", not required. Both sides of the Q are lazy subqueries over indexed columns, so they cost no extra round trip. Add a cache later if `EXPLAIN` on the hot path says so.
7. **The skip-unchanged-rows optimisation (Task 6) does not detect m2m-only changes.** `updated_at` is not bumped when an m2m membership changes (e.g. `ChatAttachment.files`), so a re-read of an unchanged row will not re-apply its m2m. This only affects re-reads — first imports always run in full — and is the trade the spec asks for.

## Task index

| # | Task | Phase |
|---|---|---|
| 1 | Index the sort key the export paginates on | 1 — prerequisites |
| 2 | Prefetch the m2m fields the export serializer queries per row | 1 |
| 3 | Export the EventAction of a scheduled trigger | 1 |
| 4 | Remap `EventAction.params["pipeline_id"]` on import | 1 |
| 5 | Give the state store WAL mode and a source-timestamp column | 1 |
| 6 | Skip re-importing a row whose source timestamp is unchanged | 1 |
| 7 | Store pagination cursors explicitly, per selection | 1 |
| 8 | `Team.exportable_experiments` and the selection helpers | 2 — the selection |
| 9 | The Migration card | 2 |
| 10 | Show the selection on the app banner | 2 |
| 11 | `ChatbotScope` — resolve one request's row sets | 3 — the scope |
| 12 | The per-model scope registry and `scoped_queryset` | 3 |
| 13 | Serve the scope from the export API | 3 |
| 14 | Teach the sync client about the selection | 3 |
| 15 | Freeze only the selected chatbots | 4 — freeze and operator surface |
| 16 | Tell the operator what was and was not synced | 4 |
| 17 | Back the files prompt off for a partial sync | 4 |
| 18 | Limit webhook re-registration to the chatbots that moved | 4 |
| 19 | Drive a partial sync end to end | 4 |

Phase 1 ships on its own: it fixes two standing bugs in the full-team sync and removes the per-page
cost the scope work would otherwise multiply. Nothing in it reads the allowlist.

## File Structure

**New files**

| File | Responsibility |
|---|---|
| `apps/teams/export/selection.py` | What the allowlist means: which chatbots may be picked, and expanding a selection to its version family. Shared by the form, the export scope, and the migration freeze. |
| `apps/teams/export/chatbot_scope.py` | `ChatbotScope` (the resolved row sets for one request) and `CHATBOT_SCOPE_REGISTRY` (per-model `Q` builders and scope class). |
| `apps/teams/export/tests/test_selection.py` | Tests for `selection.py` (Task 8). |
| `apps/teams/export/tests/test_chatbot_scope.py` | Tests for the scope sets, the per-model rules, and the partition test that forces a new model to be classified (Tasks 11–12). |
| `apps/teams/export/tests/test_manifest_indexes.py` | Pins that every `updated_at`-paged resource has a matching index (Task 1). |
| `apps/teams/export/tests/test_partial_sync_integration.py` | One selection through the real serializers and the real importer (Task 19). |
| `apps/teams/tests/test_forms.py` | Tests for the Migration card form (Task 9). |
| `apps/teams/tests/test_export_service.py` | Tests for `frozen_experiment_q()` (Task 15). |

**Modified files**

| File | Change |
|---|---|
| `apps/chat/models.py`, `apps/experiments/models.py`, `apps/pipelines/models.py`, `apps/events/models/models.py`, `apps/annotations/models.py`, `apps/assessments/models.py`, `apps/ocs_notifications/models.py` | `(updated_at, id)` index on the 10 high-volume synced models. |
| `apps/teams/models.py` | `Team.exportable_experiments`. |
| `apps/teams/model_audit_fields.py` | Audit the new field. |
| `apps/teams/export/manifest.py` | `TEAM_PATH_REGISTRY` fix, `EXCLUDE_REGISTRY` entry, `PREFETCH_REGISTRY` entries, `ManifestEntry.scope`, `scoped_queryset()`. |
| `apps/teams/export/translation.py` | `source_updated_at` column, `cursors` and `selections` tables, SQLite pragmas. |
| `apps/teams/export/importer.py` | `EventAction.params` remap; skip unchanged rows. |
| `apps/teams/export/client.py` | `iter_pages()` — the spec lists this file as unchanged, which holds for the scope work; the page-by-page cursor fix in Task 7 needs the page boundaries. |
| `apps/teams/export_service.py` | `frozen_experiment_q()` replaces `migrating_team_ids()`. |
| `apps/events/tasks.py` | Four call sites switch to `frozen_experiment_q()`. |
| `apps/api/export/views.py` | `ResourceView.get` builds and applies the scope. |
| `apps/api/export/serializers.py` | `scope` on the manifest entry; the team endpoint reports the selection. |
| `apps/teams/forms.py`, `apps/teams/views/manage_team_views.py`, `templates/teams/manage_team.html`, `templates/teams/migration_lock_banner.html` | The Migration card and the app banner. |
| `apps/teams/management/commands/sync_team.py` | Page-by-page cursors, selection key, skipping excluded resources, the files prompt, the report, the `--force-delete` refusal. |
| `apps/teams/management/commands/reregister_webhooks.py` | `--chatbot` flag. |

---

## Phase 1 — full-team sync prerequisites

Tasks 1–7 are independent of the selection feature. They fix two standing bugs and remove the cost that Phase 3 would otherwise multiply. Phase 1 is shippable on its own.

---

### Task 1: Index the sort key the export paginates on

Every `updated_at_id` resource paginates with `ORDER BY updated_at, id`, and no synced model has an index leading with `updated_at`. Each page is a scan plus a top-N sort of the whole scoped set.

The 10 models below are the ones the chatbot scope keeps (classes (a) and (b)) that use the `updated_at_id` cursor. The 13 `updated_at_id` models left out are `evaluations.*`, `human_annotations.*` and `analysis.transcriptanalysis`, which a chatbot-scoped sync serves empty.

**Files:**
- Modify: `apps/chat/models.py` (`Chat.Meta`, `ChatMessage.Meta:189-198`)
- Modify: `apps/experiments/models.py` (`Participant.Meta`, `ParticipantData.Meta`, `ExperimentSession.Meta`)
- Modify: `apps/pipelines/models.py` (`PipelineChatHistory.Meta`, near `:832`)
- Modify: `apps/events/models/models.py` (`ScheduledMessage.Meta`)
- Modify: `apps/annotations/models.py` (`UserComment.Meta`, near `:170`)
- Modify: `apps/assessments/models.py` (`Score.Meta`, near `:10`)
- Modify: `apps/ocs_notifications/models.py` (`EventUser.Meta`, near `:191`)
- Create: one `AddIndexConcurrently` migration per app

**Interfaces:**
- Consumes: nothing.
- Produces: index names `<modelname>_updated_at_id_idx` (e.g. `chatmessage_updated_at_id_idx`). No Python API.

- [ ] **Step 1: Write the failing test**

Create `apps/teams/export/tests/test_manifest_indexes.py`:

```python
"""Every resource paged by ``updated_at`` needs an index leading with it, or each page costs a
top-N sort of the whole scoped set."""

import pytest
from django.apps import apps

from apps.teams.export import manifest

# Class (c) models: a chatbot-scoped sync serves these empty, and they are small enough in a
# full-team sync that the sort is free. Reviewed when a model moves between scope classes.
UNINDEXED_UPDATED_AT_MODELS = frozenset(
    {
        "analysis.transcriptanalysis",
        "evaluations.datasetautopopulationrule",
        "evaluations.evaluationconfig",
        "evaluations.evaluationdataset",
        "evaluations.evaluationmessage",
        "evaluations.evaluationrun",
        "evaluations.evaluationrunaggregate",
        "evaluations.evaluator",
        "evaluations.evaluatortagrule",
        "human_annotations.annotation",
        "human_annotations.annotationitem",
        "human_annotations.annotationqueue",
        "human_annotations.annotationqueueaggregate",
    }
)


def _leads_with_updated_at(model) -> bool:
    return any(list(index.fields)[:2] == ["updated_at", "id"] for index in model._meta.indexes)


@pytest.mark.parametrize(
    "entry",
    [e for e in manifest.MANIFEST_ENTRIES if e.cursor == "updated_at_id"],
    ids=lambda e: e.resource,
)
def test_updated_at_resources_have_a_matching_index(entry):
    model = apps.get_model(*entry.model.split("."))
    if entry.model in UNINDEXED_UPDATED_AT_MODELS:
        pytest.skip(f"{entry.model} is deliberately unindexed")
    assert _leads_with_updated_at(model), f"{entry.model} needs models.Index(fields=['updated_at', 'id'])"


def test_unindexed_list_only_names_synced_models():
    synced = {e.model for e in manifest.MANIFEST_ENTRIES}
    assert UNINDEXED_UPDATED_AT_MODELS <= synced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_manifest_indexes.py -v`
Expected: FAIL — 10 parametrized cases fail with "needs models.Index(...)".

- [ ] **Step 3: Add the index to each model's Meta**

Add this entry to the `indexes` list of each of the 10 models (creating `class Meta` with `indexes = [...]` where the model has no `Meta`, and keeping every existing index):

```python
        indexes = [
            # ... existing indexes ...
            # The export API pages every resource by (updated_at, id); without this each page is a
            # top-N sort of the whole table.
            models.Index(fields=["updated_at", "id"], name="chatmessage_updated_at_id_idx"),
        ]
```

Use these explicit names (Django's auto-generated names are hash-suffixed and unreadable in `EXPLAIN` output):

| Model | Index name |
|---|---|
| `chat.Chat` | `chat_updated_at_id_idx` |
| `chat.ChatMessage` | `chatmessage_updated_at_id_idx` |
| `experiments.Participant` | `participant_updated_at_id_idx` |
| `experiments.ParticipantData` | `participantdata_updated_at_id_idx` |
| `experiments.ExperimentSession` | `expsession_updated_at_id_idx` |
| `pipelines.PipelineChatHistory` | `pipechathistory_updated_at_id_idx` |
| `events.ScheduledMessage` | `schedmessage_updated_at_id_idx` |
| `annotations.UserComment` | `usercomment_updated_at_id_idx` |
| `assessments.Score` | `score_updated_at_id_idx` |
| `ocs_notifications.EventUser` | `eventuser_updated_at_id_idx` |

- [ ] **Step 4: Generate the migrations**

```bash
uv run python manage.py makemigrations chat experiments pipelines events annotations assessments ocs_notifications
```

- [ ] **Step 5: Rewrite each generated migration to add the index concurrently**

Each generated file uses `AddIndex` inside an atomic migration, which takes an `ACCESS EXCLUSIVE` lock for the duration of the build. Edit every generated file to this shape (example: `apps/chat/migrations/00NN_updated_at_id_indexes.py`):

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0028_alter_chatmessage_external_ids"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="chat",
            index=models.Index(fields=["updated_at", "id"], name="chat_updated_at_id_idx"),
        ),
        AddIndexConcurrently(
            model_name="chatmessage",
            index=models.Index(fields=["updated_at", "id"], name="chatmessage_updated_at_id_idx"),
        ),
    ]
```

- [ ] **Step 6: Run the tests and the migrations**

Run: `uv run python manage.py migrate && uv run pytest apps/teams/export/tests/test_manifest_indexes.py -v`
Expected: PASS (10 passed, 13 skipped, 1 passed for the list check).

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run inv ruff --paths apps/chat apps/experiments apps/pipelines apps/events apps/annotations apps/assessments apps/ocs_notifications apps/teams/export/tests/test_manifest_indexes.py
uv run inv typecheck --python --paths apps/teams/export
git add -A
git commit -m "perf: index (updated_at, id) on the models the export pages by it"
```

---

### Task 2: Prefetch the m2m fields the export serializer queries per row

`build_resource_serializer` uses `fields = "__all__"`, so each m2m becomes a `PrimaryKeyRelatedField(many=True)` that queries once per row: a 100-row page of `chat_attachments` issues 101 queries. `PREFETCH_REGISTRY` (`apps/teams/export/manifest.py:193`) exists for this and holds one entry.

Affected: `chat.chatattachment.files`, `pipelines.node.collection_indexes`, `human_annotations.annotationqueue.assignees`. `tags` on `Chat`/`ChatMessage` is not affected — taggit's manager is not serialized and tags travel as `custom_tagged_items`.

**Files:**
- Modify: `apps/teams/export/manifest.py:184-195`
- Test: `apps/teams/export/tests/test_manifest.py`

**Interfaces:**
- Consumes: `PREFETCH_REGISTRY: dict[str, Callable[[object], list]]` — maps a model label to a factory taking the team and returning a list of prefetch arguments.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/export/tests/test_manifest.py`:

```python
@pytest.mark.django_db()
def test_m2m_resources_are_prefetched():
    """Serializing an m2m field with fields="__all__" queries once per row. The prefetch keeps a
    page at a constant number of queries instead of one per row."""
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    from apps.utils.factories.chat import ChatFactory, ChatMessageFactory

    team = TeamFactory()
    for _ in range(4):
        chat = ChatFactory(team=team)
        attachment = chat.attachments.create(tool_type="code_interpreter")
        attachment.files.add(FileFactory(team=team))

    entry = manifest.get_manifest_entry("chat_attachments")
    serializer = build_resource_serializer(manifest.entry_model(entry.model))
    queryset = manifest.team_scoped_queryset(entry, team)
    with CaptureQueriesContext(connection) as captured:
        serializer(list(queryset), many=True, context={"team": team, "public_key": None}).data
    assert len(captured) <= 2, [q["sql"] for q in captured]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_manifest.py::test_m2m_resources_are_prefetched -v`
Expected: FAIL — 5 queries captured (one per row plus the outer query).

- [ ] **Step 3: Add the prefetch factories**

In `apps/teams/export/manifest.py`, below `_customuser_prefetch`:

```python
def _prefetch(*names: str) -> Callable[[object], list]:
    """A prefetch factory for fields that need no team scoping."""
    return lambda _team: list(names)
```

and extend the registry:

```python
# Per-model prefetches, built per request because some are scoped to the team being synced.
# An m2m field serialized by ``fields = "__all__"`` queries once per row without one.
PREFETCH_REGISTRY: dict[str, Callable[[object], list]] = {
    "users.customuser": _customuser_prefetch,
    "chat.chatattachment": _prefetch("files"),
    "pipelines.node": _prefetch("collection_indexes"),
    "human_annotations.annotationqueue": _prefetch("assignees"),
}
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest apps/teams/export/tests/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run inv ruff --paths apps/teams/export/manifest.py apps/teams/export/tests/test_manifest.py
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export/manifest.py apps/teams/export/tests/test_manifest.py
git commit -m "perf: prefetch the m2m fields the export serializer reads per row"
```

---

### Task 3: Export the EventAction of a scheduled trigger

`TEAM_PATH_REGISTRY["events.eventaction"]` (`apps/teams/export/manifest.py:158-161`) reaches an `EventAction` through `static_trigger` and `timeout_trigger` only. `ScheduledTrigger.action` is a non-null `OneToOneField` to the same model, so a scheduled trigger's action is never exported and importing the trigger raises `UnresolvedForeignKey` (`importer.resolve_fk:91`).

`ScheduledMessage.action` needs no fourth branch: a scheduled message's action is the same `EventAction` row as its scheduled trigger's.

**Files:**
- Modify: `apps/teams/export/manifest.py:157-161`
- Test: `apps/teams/export/tests/test_manifest.py`

**Interfaces:**
- Consumes: `manifest.TEAM_PATH_REGISTRY`, `manifest.team_scoped_queryset(entry, team)`.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/export/tests/test_manifest.py` (and add `from apps.utils.factories.events import ScheduledTriggerFactory, StaticTriggerFactory, TimeoutTriggerFactory` to the imports):

```python
@pytest.mark.django_db()
def test_event_actions_of_every_trigger_type_are_exported():
    """Each trigger type holds a non-null OneToOne to its EventAction, so an action left out of the
    export makes its trigger unimportable (resolve_fk raises on a non-null FK with no translation)."""
    team = TeamFactory()
    experiment = ExperimentFactory(team=team)
    triggers = [
        StaticTriggerFactory(experiment=experiment),
        TimeoutTriggerFactory(experiment=experiment),
        ScheduledTriggerFactory(experiment=experiment),
    ]

    entry = manifest.get_manifest_entry("event_actions")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert {trigger.action_id for trigger in triggers} <= pks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_manifest.py::test_event_actions_of_every_trigger_type_are_exported -v`
Expected: FAIL — the scheduled trigger's `action_id` is missing from `pks`.

- [ ] **Step 3: Add the third path**

In `apps/teams/export/manifest.py`, replace the `events.eventaction` entry:

```python
    # EventAction has no team FK; each trigger type holds a OneToOneField to it. A ScheduledMessage's
    # action is the same row as its scheduled trigger's, so it needs no branch of its own.
    "events.eventaction": [
        "static_trigger__experiment__team",
        "timeout_trigger__experiment__team",
        "scheduled_trigger__experiment__team",
    ],
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/export/manifest.py apps/teams/export/tests/test_manifest.py
git add apps/teams/export/manifest.py apps/teams/export/tests/test_manifest.py
git commit -m "fix: export the EventAction of a scheduled trigger"
```

---

### Task 4: Remap `EventAction.params["pipeline_id"]` on import

`Importer._remap_embedded_resource_ids` (`apps/teams/export/importer.py:431`) rewrites resource ids hidden in `pipelines.node.params` but not the ones in `events.eventaction.params`. A `pipeline_start` action arrives on the target still pointing at the *source's* pipeline id, which on the target is either a different pipeline or nothing at all.

`apps/events/versioning.py` already declares which params carry ids: `_EVENT_ACTION_PARAM_SPECS` keyed by action type, exposed as `get_event_action_param_specs(action_type)`. Drive the remap off that rather than hardcoding `"pipeline_id"`, so a new versioned param is picked up without touching the importer.

Manifest order already imports `pipelines` (`:62`) before `event_actions` (`:67`), so the translation exists by the time the action lands.

**Files:**
- Modify: `apps/teams/export/importer.py:431-435`
- Test: `apps/teams/export/tests/test_importer_transforms.py`

**Interfaces:**
- Consumes: `apps.events.versioning.get_event_action_param_specs(action_type) -> tuple[EventActionParamSpec, ...]`, each with `.param_name: str` and `.model_label: str` (`"app_label.ModelName"`).
- Produces: `importer.remap_event_action_params(params: dict, action_type: str, store: FKTranslationStore) -> dict`.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/export/tests/test_importer_transforms.py`:

```python
def test_remap_event_action_params_translates_the_pipeline_id():
    store = FakeStore({"pipelines.pipeline": {11: 99}})

    result = remap_event_action_params({"pipeline_id": 11, "input_type": "last_message"}, "pipeline_start", store)

    assert result == {"pipeline_id": 99, "input_type": "last_message"}


def test_remap_event_action_params_leaves_other_action_types_alone():
    store = FakeStore({"pipelines.pipeline": {11: 99}})

    assert remap_event_action_params({"pipeline_id": 11}, "log", store) == {"pipeline_id": 11}


def test_remap_event_action_params_keeps_an_untranslated_id():
    """A pipeline the sync never saw has no translation; leaving the id alone matches
    remap_node_params, where an unsynced reference is kept verbatim rather than nulled."""
    assert remap_event_action_params({"pipeline_id": 11}, "pipeline_start", FakeStore({})) == {"pipeline_id": 11}
```

Add `remap_event_action_params` to the file's existing `from apps.teams.export.importer import (...)` block. `FakeStore` is already defined at the top of the file (`test_importer_transforms.py:18`) and exposes the only method the transform needs, `get_target`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_importer_transforms.py -k remap_event_action -v`
Expected: FAIL with `ImportError: cannot import name 'remap_event_action_params'`.

- [ ] **Step 3: Write the transform and hook it up**

In `apps/teams/export/importer.py`, next to `remap_node_params`:

```python
def remap_event_action_params(params: dict, action_type: str, store: FKTranslationStore) -> dict:
    """Rewrite the resource ids an event action holds in its params. Which params carry an id is
    declared once in ``apps.events.versioning``; references the sync doesn't copy have no translation
    and are left as-is, matching ``remap_node_params``."""
    from apps.events.versioning import get_event_action_param_specs  # noqa: PLC0415 - models load lazily

    result = dict(params)
    for spec in get_event_action_param_specs(action_type):
        value = result.get(spec.param_name)
        if value in (None, "", 0):
            continue
        label = spec.model_label.lower()
        result[spec.param_name] = store.get_target(label, as_int(value)) or value
    return result
```

and extend the dispatch:

```python
    def _remap_embedded_resource_ids(self, model_label: str, field_values: dict) -> None:
        """Rewrite the source resource ids buried in a row's params in place. Pipeline data is
        layout-only (ADR-0046) and carries no resource ids, so it imports as-is."""
        if model_label == "pipelines.node" and "params" in field_values:
            field_values["params"] = remap_node_params(field_values["params"], self.store)
        elif model_label == "events.eventaction" and "params" in field_values:
            field_values["params"] = remap_event_action_params(
                field_values["params"], field_values.get("action_type", ""), self.store
            )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_importer_transforms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/export/importer.py apps/teams/export/tests/test_importer_transforms.py
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export/importer.py apps/teams/export/tests/test_importer_transforms.py
git commit -m "fix: translate the pipeline id in an imported event action's params"
```

---

### Task 5: Give the state store WAL mode and a source-timestamp column

`FKTranslationStore.record` commits per row (`apps/teams/export/translation.py:39`), so every imported row costs an fsync. Two changes, both groundwork for Task 6: set the SQLite pragmas, and carry the source row's `updated_at` next to its target key.

The column is added with `ALTER TABLE ... ADD COLUMN` guarded by a column check, because existing state DBs in the field were created without it.

**Files:**
- Modify: `apps/teams/export/translation.py:13-53`
- Test: `apps/teams/export/tests/test_translation.py`

**Interfaces:**
- Consumes: the `make_store` fixture in `apps/teams/export/tests/conftest.py`.
- Produces:
  - `FKTranslationStore.record(content_type: str, source_key: int, target_key: int | None = None, source_updated_at: str | None = None) -> None`
  - `FKTranslationStore.get_source_updated_at(content_type: str, source_key: int) -> str | None`

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/export/tests/test_translation.py`:

```python
def test_record_round_trips_the_source_timestamp(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chatmessage", 5, 99, source_updated_at="2026-01-02T03:04:05+00:00")
    assert store.get_source_updated_at("chat.chatmessage", 5) == "2026-01-02T03:04:05+00:00"


def test_source_timestamp_persists_across_reopen(make_store, tmp_path):
    path = tmp_path / "team.sqlite"
    make_store(path).record("chat.chatmessage", 5, 99, source_updated_at="2026-01-02T03:04:05+00:00")
    assert make_store(path).get_source_updated_at("chat.chatmessage", 5) == "2026-01-02T03:04:05+00:00"


def test_source_timestamp_is_absent_for_an_unrecorded_row(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record("chat.chatmessage", 5, 99)
    assert store.get_source_updated_at("chat.chatmessage", 5) is None
    assert store.get_source_updated_at("chat.chatmessage", 6) is None


def test_store_opens_a_database_written_before_the_timestamp_column(make_store, tmp_path):
    """State DBs created by an earlier release have no source_updated_at column; opening one must
    add it rather than fail, and the rows already in it read back as unknown."""
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE fk_translation (content_type TEXT NOT NULL, source_key INTEGER NOT NULL, "
        "target_key INTEGER, PRIMARY KEY (content_type, source_key))"
    )
    connection.execute("INSERT INTO fk_translation VALUES ('teams.team', 1, 7)")
    connection.commit()
    connection.close()

    store = make_store(path)
    assert store.get_target("teams.team", 1) == 7
    assert store.get_source_updated_at("teams.team", 1) is None


def test_store_uses_wal_journaling(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: FAIL — `record()` rejects the `source_updated_at` keyword and `get_source_updated_at` does not exist.

- [ ] **Step 3: Rewrite the store's schema handling**

Replace `FKTranslationStore.__init__` and `record` in `apps/teams/export/translation.py`:

```python
class FKTranslationStore:
    def __init__(self, path):
        """Open (creating if needed) the SQLite store at ``path`` and load its table into an
        in-memory index for fast lookups."""
        self._conn = sqlite3.connect(str(path))
        # A sync commits per row. WAL keeps crash consistency while removing the per-row fsync that
        # rollback journalling costs; synchronous=NORMAL is WAL's safe setting (a crash can lose the
        # last transactions, which a rerun re-fetches).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS fk_translation ("
            "content_type TEXT NOT NULL, source_key INTEGER NOT NULL, target_key INTEGER, "
            "source_updated_at TEXT, "
            "PRIMARY KEY (content_type, source_key))"
        )
        self._conn.execute("CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY)")
        self._add_missing_columns()
        self._conn.commit()
        self._index: dict[str, dict[int, int | None]] = {}
        self._source_timestamps: dict[str, dict[int, str | None]] = {}
        for content_type, source_key, target_key, source_updated_at in self._conn.execute(
            "SELECT content_type, source_key, target_key, source_updated_at FROM fk_translation"
        ):
            self._index.setdefault(content_type, {})[source_key] = target_key
            self._source_timestamps.setdefault(content_type, {})[source_key] = source_updated_at

    def _add_missing_columns(self) -> None:
        """Bring a store created by an earlier release up to the current schema. CREATE TABLE IF NOT
        EXISTS leaves an existing table alone, so a column added later has to be added by hand."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(fk_translation)")}
        if "source_updated_at" not in existing:
            self._conn.execute("ALTER TABLE fk_translation ADD COLUMN source_updated_at TEXT")

    def record(
        self,
        content_type: str,
        source_key: int,
        target_key: int | None = None,
        source_updated_at: str | None = None,
    ) -> None:
        """Upsert a source->target mapping. A null ``target_key`` is the checkpoint marker meaning
        "synced but not yet created on the target"; it's filled in once the row exists.
        ``source_updated_at`` is the source row's timestamp verbatim, used to skip a re-read of a row
        that hasn't changed."""
        self._conn.execute(
            "INSERT INTO fk_translation (content_type, source_key, target_key, source_updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (content_type, source_key) DO UPDATE SET "
            "target_key = excluded.target_key, source_updated_at = excluded.source_updated_at",
            (content_type, source_key, target_key, source_updated_at),
        )
        self._conn.commit()
        self._index.setdefault(content_type, {})[source_key] = target_key
        self._source_timestamps.setdefault(content_type, {})[source_key] = source_updated_at

    def get_source_updated_at(self, content_type: str, source_key: int) -> str | None:
        """The source ``updated_at`` recorded with the row, or None when it's unknown."""
        return self._source_timestamps.get(content_type, {}).get(source_key)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/export/translation.py apps/teams/export/tests/test_translation.py
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export/translation.py apps/teams/export/tests/test_translation.py
git commit -m "perf: carry the source timestamp in the sync state store and enable WAL"
```

---

### Task 6: Skip re-importing a row whose source timestamp is unchanged

`Importer._get_or_create` (`apps/teams/export/importer.py:340`) on an already-synced row runs `.exists()`, `.get()`, `instance.save()` and a second `UPDATE` from `_bypass_auto_now_update`, inside a per-row transaction, plus a SQLite commit. Re-reading millions of rows is hours of redo. With Task 5's column, a re-read of an unchanged row becomes a dict lookup.

The skip applies only when all three hold: the row carries an `updated_at`, the store already has a non-null target for it, and the recorded timestamp is byte-identical to the incoming one. It does not detect an m2m-only change, which does not bump `updated_at` — see deviation 7.

**Files:**
- Modify: `apps/teams/export/importer.py:215-254`
- Test: `apps/teams/export/tests/test_importer.py`

**Interfaces:**
- Consumes: `FKTranslationStore.get_source_updated_at`, `FKTranslationStore.has_target`, `FKTranslationStore.record(..., source_updated_at=...)`.
- Produces: `Importer.skipped_rows: int` — a counter the sync report prints.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/export/tests/test_importer.py`:

```python
def test_unchanged_row_is_skipped_on_reimport(make_store, tmp_path):
    """A second pass over a row whose source updated_at hasn't moved must not touch the database."""
    store = make_store(tmp_path / "team.sqlite")
    importer = Importer(store)
    team = TeamFactory()
    importer.set_target_team(team)

    row = {"id": 5, "name": "OpenAI", "type": "openai", "config": {}, "created_at": PAST, "updated_at": PAST}
    importer.import_rows("service_providers.llmprovider", [row])
    target_pk = store.get_target("service_providers.llmprovider", 5)

    with django_assert_num_queries(0):
        assert importer.import_rows("service_providers.llmprovider", [row]) == 0
    assert importer.skipped_rows == 1
    assert store.get_target("service_providers.llmprovider", 5) == target_pk


def test_changed_row_is_reimported(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    importer = Importer(store)
    team = TeamFactory()
    importer.set_target_team(team)

    row = {"id": 5, "name": "OpenAI", "type": "openai", "config": {}, "created_at": PAST, "updated_at": PAST}
    importer.import_rows("service_providers.llmprovider", [row])

    changed = {**row, "name": "OpenAI (renamed)", "updated_at": "2026-02-02T03:04:05+00:00"}
    assert importer.import_rows("service_providers.llmprovider", [changed]) == 1
    assert LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5)).name == "OpenAI (renamed)"


def test_row_without_a_target_is_imported_even_when_the_timestamp_matches(make_store, tmp_path):
    """An interrupted run leaves a checkpoint with a null target. The row does not exist on the
    target yet, so a matching timestamp must not skip it."""
    store = make_store(tmp_path / "team.sqlite")
    store.record("service_providers.llmprovider", 5, None, source_updated_at=PAST)
    importer = Importer(store)
    importer.set_target_team(TeamFactory())

    row = {"id": 5, "name": "OpenAI", "type": "openai", "config": {}, "created_at": PAST, "updated_at": PAST}
    assert importer.import_rows("service_providers.llmprovider", [row]) == 1
```

Use the `django_assert_num_queries` fixture from pytest-django; add `PAST = "2020-01-02T03:04:05+00:00"` and the `LlmProvider` / `TeamFactory` imports if the file does not already have them.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_importer.py -k "unchanged_row or changed_row or without_a_target" -v`
Expected: FAIL — `Importer` has no `skipped_rows`, and the second pass re-saves the row.

- [ ] **Step 3: Add the skip**

In `apps/teams/export/importer.py`, add the counter to `Importer.__init__`:

```python
        # Rows a rerun read again but didn't have to touch (source updated_at unchanged).
        self.skipped_rows = 0
```

and rewrite `import_rows` plus the recording call:

```python
def import_rows(self, model_label: str, rows: Iterable[dict]) -> int:
    """Import every row for one model, unsealing its secret fields first when we hold the key.
    Returns the number of rows imported this pass (for the sync's progress report)."""
    model = entry_model(model_label)
    secret_fields = SECRET_REGISTRY.get(model_label, [])
    count = 0
    for row in rows:
        if self._is_unchanged(model_label, row):
            self.skipped_rows += 1
            continue
        if self.private_key and secret_fields:
            row = unseal_secrets(row, secret_fields, self.private_key)
        self._import_row(model_label, model, row)
        count += 1
    return count


def _is_unchanged(self, model_label: str, row: dict) -> bool:
    """Whether the row is already on the target at this exact source revision. Pagination can
    re-serve a row (a cursor reset, or an ``updated_at`` page overlapping), and re-importing one
    costs four statements and an fsync; comparing the stored source timestamp makes that a dict
    lookup. An m2m membership change doesn't move ``updated_at``, so a row whose only change is an
    m2m is not re-read."""
    source_updated_at = row.get("updated_at")
    if source_updated_at is None:
        return False
    source_pk = row["id"]
    if not self.store.has_target(model_label, source_pk):
        return False
    return self.store.get_source_updated_at(model_label, source_pk) == source_updated_at
```

In `_import_team_owned_row`, pass the timestamp through:

```python
            self.store.record(model_label, source_pk, instance.pk, source_updated_at=row.get("updated_at"))
```

and in `_import_row`'s global branch:

```python
            self.store.record(model_label, source_pk, match.pk, source_updated_at=row.get("updated_at"))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/export/importer.py apps/teams/export/tests/test_importer.py
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export/importer.py apps/teams/export/tests/test_importer.py
git commit -m "perf: skip re-importing a row whose source timestamp is unchanged"
```

---

### Task 7: Store pagination cursors explicitly, per selection

`_start_cursor` (`apps/teams/management/commands/sync_team.py:90`) derives each model's cursor from the rows already committed for it. That is correct only while the source serves one fixed set of rows: sync chatbot A, then chatbot B against the same store, and the derived pk cursor is `max(A's ids)`, so every B row with a lower id is skipped. It is also slow — building `pk__in=<every committed target pk>` was measured at 6.6s for 1M ids per `updated_at_id` model per run.

Replace it with a `cursors` table keyed by `(selection_key, model_label)`, written after each page's rows commit. The FK translation table stays shared across selections — that is what lets a second chatbot reuse the providers and files the first one created.

This task introduces the `selection_key` column and always passes `"all"`. Task 16 computes a real key.

**Files:**
- Modify: `apps/teams/export/translation.py`
- Modify: `apps/teams/export/client.py:53-60`
- Modify: `apps/teams/management/commands/sync_team.py:90-102,237-271`
- Test: `apps/teams/export/tests/test_translation.py`, `apps/teams/export/tests/test_client.py`, `apps/teams/export/tests/test_command.py`

**Interfaces:**
- Consumes: `derive_pk_cursor(source_keys) -> str | None` and `derive_updated_at_cursor(rows) -> str | None` (`apps/teams/export/translation.py:83,87`), both already present.
- Produces:
  - `FKTranslationStore.get_cursor(selection_key: str, model_label: str) -> str | None`
  - `FKTranslationStore.set_cursor(selection_key: str, model_label: str, cursor: str | None) -> None`
  - `FKTranslationStore.cursors_for(selection_key: str) -> dict[str, str | None]`
  - `translation.page_cursor(cursor_type: str, rows: list[dict]) -> str | None`
  - `ResourceFetcher.iter_pages(resource, start_cursor=None, limit=100) -> Iterator[list[dict]]`
  - `ALL_CHATBOTS_KEY = "all"` in `apps/teams/export/translation.py`

- [ ] **Step 1: Write the failing store test**

Append to `apps/teams/export/tests/test_translation.py`:

```python
from apps.teams.export.translation import ALL_CHATBOTS_KEY, page_cursor


def test_cursor_round_trips(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") is None
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"


def test_cursors_are_kept_per_selection(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    store.set_cursor("deadbeef", "chat.chatmessage", "xyz")
    assert store.get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"
    assert store.get_cursor("deadbeef", "chat.chatmessage") == "xyz"


def test_cursors_persist_across_reopen(make_store, tmp_path):
    path = tmp_path / "team.sqlite"
    make_store(path).set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "abc")
    assert make_store(path).get_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage") == "abc"


def test_cursors_for_returns_one_selection(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chat", "one")
    store.set_cursor(ALL_CHATBOTS_KEY, "chat.chatmessage", "two")
    store.set_cursor("deadbeef", "chat.chat", "three")
    assert store.cursors_for(ALL_CHATBOTS_KEY) == {"chat.chat": "one", "chat.chatmessage": "two"}


def test_page_cursor_for_a_pk_resource():
    assert page_cursor("pk", [{"id": 3}, {"id": 7}]) == "7"
    assert page_cursor("pk", []) is None


def test_page_cursor_for_an_updated_at_resource():
    rows = [
        {"id": 3, "updated_at": "2026-01-01T00:00:00+00:00"},
        {"id": 7, "updated_at": "2026-01-02T00:00:00+00:00"},
    ]
    keyset = json.loads(base64.b64decode(page_cursor("updated_at_id", rows)))
    assert keyset == {"updated_at": "2026-01-02T00:00:00+00:00", "id": 7}
    assert page_cursor("updated_at_id", []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: FAIL with `ImportError: cannot import name 'ALL_CHATBOTS_KEY'`.

- [ ] **Step 3: Add the cursors table and the page-cursor helper**

In `apps/teams/export/translation.py`, add the module constant and, inside `__init__` next to the other `CREATE TABLE` statements:

```python
# The cursor namespace used when the source exports the whole team.
ALL_CHATBOTS_KEY = "all"
```

```python
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cursors ("
            "selection_key TEXT NOT NULL, model_label TEXT NOT NULL, cursor TEXT, "
            "PRIMARY KEY (selection_key, model_label))"
        )
```

and the accessors (cursors are read a few dozen times per run, so they go straight to SQLite rather than into an in-memory index):

```python
def get_cursor(self, selection_key: str, model_label: str) -> str | None:
    """Where to resume this model's pull. Cursors are namespaced by selection: the source serves
    a different row set per chatbot selection, so a cursor from one selection would skip rows
    that a wider one puts below it."""
    row = self._conn.execute(
        "SELECT cursor FROM cursors WHERE selection_key = ? AND model_label = ?",
        (selection_key, model_label),
    ).fetchone()
    return row[0] if row else None


def set_cursor(self, selection_key: str, model_label: str, cursor: str | None) -> None:
    self._conn.execute(
        "INSERT INTO cursors (selection_key, model_label, cursor) VALUES (?, ?, ?) "
        "ON CONFLICT (selection_key, model_label) DO UPDATE SET cursor = excluded.cursor",
        (selection_key, model_label, cursor),
    )
    self._conn.commit()


def cursors_for(self, selection_key: str) -> dict[str, str | None]:
    return {
        model_label: cursor
        for model_label, cursor in self._conn.execute(
            "SELECT model_label, cursor FROM cursors WHERE selection_key = ?", (selection_key,)
        )
    }
```

At the bottom of the module, next to the existing derive helpers:

```python
def page_cursor(cursor_type: str, rows: list[dict]) -> str | None:
    """The resume cursor for the page just imported, derived from its own rows.

    The endpoint only returns a cursor while more rows remain, so the last page of a run would leave
    nothing to resume from. Deriving it here keeps a rerun starting after the last row it committed."""
    if not rows:
        return None
    if cursor_type == "pk":
        return derive_pk_cursor(row["id"] for row in rows)
    return derive_updated_at_cursor((parse_datetime(row["updated_at"]), row["id"]) for row in rows)
```

with `from django.utils.dateparse import parse_datetime` added to the module imports.

- [ ] **Step 4: Run the store tests**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing client test**

Append to `apps/teams/export/tests/test_client.py`:

```python
def test_iter_pages_yields_each_page_separately():
    """The sync records its cursor after each page commits, so it needs the pages, not a flat row
    stream."""
    session = FakeSession(
        [
            {"results": [{"id": 1}, {"id": 2}], "cursor": "c1", "has_more": True},
            {"results": [{"id": 3}], "cursor": None, "has_more": False},
        ]
    )
    fetcher = ResourceFetcher("https://src", "key", session=session, sleep=lambda _s: None)

    assert list(fetcher.iter_pages("chats")) == [[{"id": 1}, {"id": 2}], [{"id": 3}]]
```

Reuse whatever fake session helper `test_client.py` already defines; if it builds responses inline, follow that shape instead of `FakeSession`.

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_client.py -k iter_pages -v`
Expected: FAIL with `AttributeError: 'ResourceFetcher' object has no attribute 'iter_pages'`.

- [ ] **Step 7: Add `iter_pages` and express `iter_rows` in terms of it**

In `apps/teams/export/client.py`:

```python
def iter_pages(self, resource, start_cursor=None, limit=100):
    """Yield each page's rows as its own list. The sync writes its resume cursor once a page's
    rows are committed, so it has to see the page boundaries."""
    cursor = start_cursor
    while True:
        page = self.get_page(resource, cursor, limit)
        yield page["results"]
        if not page.get("has_more"):
            return
        cursor = page["cursor"]


def iter_rows(self, resource, start_cursor=None, limit=100):
    for rows in self.iter_pages(resource, start_cursor=start_cursor, limit=limit):
        yield from rows
```

- [ ] **Step 8: Run the client tests**

Run: `uv run pytest apps/teams/export/tests/test_client.py -v`
Expected: PASS.

- [ ] **Step 9: Write the failing command test**

Append to `apps/teams/export/tests/test_command.py`:

```python
def test_run_sync_resumes_from_the_stored_cursor(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "service_providers.llmprovider", "42")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", "42") in client.iter_calls


def test_run_sync_records_a_cursor_after_each_page(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")

    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    assert store.get_cursor(ALL_CHATBOTS_KEY, "service_providers.llmprovider") == "5"
```

Update `FakeClient` in the same file to serve pages:

```python
    def iter_pages(self, resource, start_cursor=None, limit=100):
        self.iter_calls.append((resource, start_cursor))
        rows = list(self.rows_by_resource.get(resource, []))
        return iter([rows]) if rows else iter([])
```

and add `ALL_CHATBOTS_KEY` to the file's `apps.teams.export.translation` import.

- [ ] **Step 10: Run it to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_command.py -k cursor -v`
Expected: FAIL — `run_sync` still calls `iter_rows` and derives its cursor from committed rows.

- [ ] **Step 11: Rewire `run_sync` and delete `_start_cursor`**

In `apps/teams/management/commands/sync_team.py`, delete `_start_cursor` (`:90-102`) and the now-unused `derive_pk_cursor` / `derive_updated_at_cursor` imports, then replace the manifest loop:

```python
from apps.teams.export.translation import ALL_CHATBOTS_KEY, FKTranslationStore, page_cursor
```

```python
def run_sync(
    client,
    store,
    private_key,
    write=lambda _m: None,
    page_limit=100,
    enforce_schema=True,
    on_user_created=send_password_reset_email,
    style=None,
    cursor_key=ALL_CHATBOTS_KEY,
):
    manifest = check_sync_preconditions(client, private_key, enforce_schema)

    importer = Importer(
        store,
        private_key=private_key,
        on_user_created=on_user_created,
        fetch_file_content=client.get_file_content,
    )
    try:
        with mute_signals():
            load_team(importer, client, store)
            for entry in manifest["entries"]:
                count = _sync_resource(importer, client, store, entry, page_limit, cursor_key)
                write(_style_synced_line(f"synced {count} {entry['resource']} rows", count, style))
    except requests.HTTPError as exc:
        friendly = _friendly_http_error_message(exc)
        if friendly is None:
            raise
        raise CommandError(friendly) from exc
    return importer


def _sync_resource(importer, client, store, entry, page_limit, cursor_key) -> int:
    """Import one resource page by page, recording the resume cursor once each page's rows are
    committed. The cursor is stored rather than derived from the synced rows: a derived one is only
    correct while the source serves a fixed row set, and it silently skips rows when the set widens."""
    model_label, resource, cursor_type = entry["model"], entry["resource"], entry["cursor"]
    cursor = store.get_cursor(cursor_key, model_label)
    count = 0
    for rows in client.iter_pages(resource, start_cursor=cursor, limit=page_limit):
        count += importer.import_rows(model_label, rows)
        next_cursor = page_cursor(cursor_type, rows)
        if next_cursor is not None:
            store.set_cursor(cursor_key, model_label, next_cursor)
    return count
```

- [ ] **Step 12: Run the whole export suite**

Run: `uv run pytest apps/teams/export/tests/ apps/api/export/tests/ -v`
Expected: PASS. A pre-existing state DB has no cursors, so the first run after this lands re-reads every resource from the start — Task 6 makes that a re-read, not a re-import.

- [ ] **Step 13: Commit**

```bash
uv run inv ruff --paths apps/teams/export apps/teams/management/commands/sync_team.py
uv run inv typecheck --python --paths apps/teams
git add apps/teams/export apps/teams/management/commands/sync_team.py
git commit -m "fix: store sync cursors per selection instead of deriving them from synced rows"
```

---

## Phase 2 — the selection

---

### Task 8: `Team.exportable_experiments` and the selection helpers

The selection lives on the team, on the source server. Empty means "export everything" — stored exactly as the admin picked it, so a chatbot created after the selection is saved is *not* exportable when a selection is active, and *is* when none is.

An M2M rather than an `ArrayField` of ids: FK integrity, cascade on chatbot delete, and it composes into the scope subqueries directly. A new table, so the migration is backwards compatible.

It must be excluded from the export. It is per-server operational state, and `load_team` imports the team row before any experiment exists, so the importer could not translate the FKs anyway.

**Files:**
- Modify: `apps/teams/models.py:73-81` (next to `is_migrating`)
- Modify: `apps/teams/model_audit_fields.py:1`
- Modify: `apps/teams/export/manifest.py:123`
- Create: `apps/teams/migrations/0019_team_exportable_experiments.py`
- Create: `apps/teams/export/selection.py`
- Test: `apps/teams/export/tests/test_selection.py`, `apps/teams/export/tests/test_manifest.py`

**Interfaces:**
- Consumes: `apps.api.v2.lookups.working_chatbots(team) -> QuerySet[Experiment]` (`apps/api/v2/lookups.py:20`).
- Produces, in `apps/teams/export/selection.py`:
  - `selected_experiment_ids(team) -> list[int]` — the raw allowlist (working versions only).
  - `expand_to_family(experiment_ids: Sequence[int]) -> QuerySet[Experiment]` — those chatbots plus every version of them, archived included.
  - `selectable_chatbots(team) -> QuerySet[Experiment]` — what the picker may offer and the form may accept.
- Produces, on the model: `Team.exportable_experiments` (M2M to `experiments.Experiment`, `blank=True`, `related_name="+"`).

- [ ] **Step 1: Write the failing test**

Create `apps/teams/export/tests/test_selection.py`:

```python
import pytest

from apps.teams.export.selection import expand_to_family, selectable_chatbots, selected_experiment_ids
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def test_selected_experiment_ids_is_empty_by_default():
    assert selected_experiment_ids(TeamFactory()) == []


def test_selected_experiment_ids_returns_the_allowlist():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    assert selected_experiment_ids(team) == [chatbot.id]


def test_expand_to_family_includes_published_and_archived_versions():
    """Selecting a chatbot selects the whole family. The published copies carry a self-referential
    working_version FK, so exporting one without its working version leaves an unresolvable link."""
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)
    archived = ExperimentFactory(team=team, working_version=working, is_archived=True)
    unrelated = ExperimentFactory(team=team)

    family = set(expand_to_family([working.id]).values_list("id", flat=True))

    assert family == {working.id, published.id, archived.id}
    assert unrelated.id not in family


def test_expand_to_family_of_nothing_is_empty():
    ExperimentFactory()
    assert list(expand_to_family([])) == []


def test_selectable_chatbots_offers_working_chatbots_only():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    ExperimentFactory(team=team, working_version=working)
    ExperimentFactory()  # another team's

    assert set(selectable_chatbots(team).values_list("id", flat=True)) == {working.id}


def test_selectable_chatbots_keeps_an_already_selected_archived_chatbot():
    """working_chatbots() filters archived rows out of the picker, not out of the allowlist. Without
    this union the archived chatbot fails the form's queryset validation and is dropped on save."""
    team = TeamFactory()
    archived = ExperimentFactory(team=team, is_archived=True)
    team.exportable_experiments.add(archived)

    assert archived.id in set(selectable_chatbots(team).values_list("id", flat=True))


def test_selectable_chatbots_omits_an_archived_chatbot_that_was_never_selected():
    team = TeamFactory()
    ExperimentFactory(team=team, is_archived=True)
    assert list(selectable_chatbots(team)) == []
```

Add to `apps/teams/export/tests/test_manifest.py`:

```python
def test_the_allowlist_is_not_exported():
    """The allowlist is per-server operational state, and load_team imports the team before any
    experiment exists, so the importer could not translate its FKs."""
    assert "exportable_experiments" in manifest.EXCLUDE_REGISTRY["teams.team"]
```

Add to `apps/teams/tests/test_migration_lock.py`, alongside `test_toggling_is_migrating_is_audited`:

```python
@pytest.mark.django_db()
def test_changing_the_allowlist_is_audited():
    """What may leave this server is a security-relevant setting, so a change to it is recorded
    next to is_migrating and public_key."""
    from apps.utils.factories.experiment import ExperimentFactory

    with enable_audit():
        team = TeamFactory()
        team.exportable_experiments.add(ExperimentFactory(team=team))
        events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
        assert any("exportable_experiments" in (e.delta or {}) for e in events)
```

If `field_audit` does not emit an m2m delta for this field the way it does for `Flag.teams`
(`FLAG_FIELDS` in `apps/teams/model_audit_fields.py` audits three m2m fields), check that
`audit_special_queryset_writes=True` on `@audit_fields` covers `.add()` here too; if it genuinely
cannot, drop this test and say so in the commit message rather than leaving a failing assertion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest apps/teams/export/tests/test_selection.py apps/teams/export/tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.teams.export.selection`.

- [ ] **Step 3: Add the field**

In `apps/teams/models.py`, after `is_migrating`:

```python
    exportable_experiments = models.ManyToManyField(
        "experiments.Experiment",
        blank=True,
        # No reverse relation: nothing reads this from the chatbot side, and the default reverse name
        # would clash with Experiment's own team relations.
        related_name="+",
        help_text=(
            "Chatbots this team may export during a migration. Empty means the whole team is "
            "exportable. Holds working versions; each one's published versions are included."
        ),
    )
```

In `apps/teams/model_audit_fields.py`:

```python
TEAM_FIELDS = [
    "name",
    "slug",
    "created_by",
    "public_key",
    "metadata",
    "is_migrating",
    "require_mfa",
    "exportable_experiments",
]
```

In `apps/teams/export/manifest.py`:

```python
    "teams.team": [
        "members",
        "public_key",
        "files_export",
        "files_export_task_id",
        "is_migrating",
        "exportable_experiments",
    ],
```

- [ ] **Step 4: Generate and inspect the migration**

```bash
uv run python manage.py makemigrations teams
```

The generated migration must be a bare `AddField` for an M2M (a new join table, nothing added to `teams_team`). Confirm there is no `AlterField` on an existing column and no data migration. Then:

```bash
uv run python manage.py migrate
```

- [ ] **Step 5: Write the selection module**

Create `apps/teams/export/selection.py`:

```python
"""What a team's chatbot allowlist means.

One definition of "which chatbots may be picked" and "what picking one includes", shared by the
team settings form, the export scope, and the migration freeze.
"""

from collections.abc import Sequence

from django.db.models import Q, QuerySet

from apps.experiments.models import Experiment
from apps.teams.models import Team


def selected_experiment_ids(team: Team) -> list[int]:
    """The team's allowlist, as stored: working versions only. Empty means the whole team."""
    return list(team.exportable_experiments.values_list("id", flat=True))


def expand_to_family(experiment_ids: Sequence[int]) -> QuerySet[Experiment]:
    """The selected chatbots plus every version of each one.

    Reads through ``_base_manager`` so archived versions are included: the export shares them along
    with the live ones, and a published copy whose working version is missing cannot resolve its
    self-referential ``working_version`` FK on the target.
    """
    experiment_ids = list(experiment_ids)
    if not experiment_ids:
        return Experiment._base_manager.none()
    return Experiment._base_manager.filter(Q(pk__in=experiment_ids) | Q(working_version_id__in=experiment_ids))


def selectable_chatbots(team: Team) -> QuerySet[Experiment]:
    """The chatbots the allowlist picker offers, and the queryset that validates a submitted one.

    Working versions only, so a published version's id can't be submitted. Archived chatbots are
    offered only when they are already in the allowlist: dropping one from the queryset would fail
    validation and silently remove it on the next save, and migrating an archived chatbot is a
    reasonable thing to want.
    """
    return Experiment._base_manager.filter(team=team, working_version__isnull=True).filter(
        Q(is_archived=False) | Q(pk__in=team.exportable_experiments.values("pk"))
    )
```

If importing `apps.experiments.models` at module level raises a circular import, swap both references for `apps.get_model("experiments", "Experiment")` inside the functions and say so in a comment — that is the only reason this module may use a local import.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_selection.py apps/teams/export/tests/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 7: Check the team row still imports**

Run: `uv run pytest apps/teams/export/tests/test_command.py apps/api/export/tests/test_views.py -v`
Expected: PASS. The team endpoint would otherwise serialize the new m2m as a list of source experiment pks and `_build_m2m_values` would raise `UnresolvedForeignKey` at team-import time; `EXCLUDE_REGISTRY` drops it from the row before that loop runs.

- [ ] **Step 8: Commit**

```bash
uv run inv ruff --paths apps/teams
uv run inv typecheck --python --paths apps/teams
git add apps/teams
git commit -m "feat: add the per-team chatbot export allowlist"
```

---

### Task 9: The Migration card

The selection goes on the existing **Migration public key** card in the *Data & migration* section of team settings (`templates/teams/manage_team.html`, `#data`, team admins only). It stays one card and one form, because the key, the scope and the mode are set together before a migration starts. The card is renamed **Migration** and the button **Save**.

A radio makes "empty means everything" explicit rather than relying on an empty picker.

**Files:**
- Modify: `apps/teams/forms.py:146-176` (`TeamPublicKeyForm`)
- Modify: `apps/teams/views/manage_team_views.py:179-199` (`set_public_key`)
- Modify: `templates/teams/manage_team.html` (the `Migration public key` card)
- Test: `apps/teams/tests/test_forms.py`, `apps/teams/tests/test_manage_team_view.py`

**Interfaces:**
- Consumes: `apps.teams.export.selection.selectable_chatbots(team)`.
- Produces: `TeamPublicKeyForm` gains `export_scope` (a `ChoiceField` of `EXPORT_SCOPE_ALL = "all"` / `EXPORT_SCOPE_SELECTED = "selected"`, both module constants in `apps/teams/forms.py`) and `exportable_experiments` (a `ModelMultipleChoiceField`).

- [ ] **Step 1: Write the failing form tests**

Create or append to `apps/teams/tests/test_forms.py`:

```python
import pytest

from apps.teams.forms import EXPORT_SCOPE_ALL, EXPORT_SCOPE_SELECTED, TeamPublicKeyForm
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def _post(team, **overrides):
    data = {"public_key": "", "export_scope": EXPORT_SCOPE_ALL}
    data.update(overrides)
    return TeamPublicKeyForm(data, instance=team)


def test_all_chatbots_clears_the_allowlist():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)

    form = _post(team, export_scope=EXPORT_SCOPE_ALL, exportable_experiments=[chatbot.id])
    assert form.is_valid(), form.errors
    form.save()

    assert list(team.exportable_experiments.all()) == []


def test_only_selected_saves_the_picked_chatbots():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[chatbot.id])
    assert form.is_valid(), form.errors
    form.save()

    assert list(team.exportable_experiments.all()) == [chatbot]


def test_only_selected_with_nothing_picked_is_an_error():
    team = TeamFactory()
    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[])
    assert not form.is_valid()
    assert "Pick at least one chatbot" in str(form.errors["exportable_experiments"])


def test_a_published_version_cannot_be_submitted():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[published.id])
    assert not form.is_valid()


def test_another_teams_chatbot_cannot_be_submitted():
    team = TeamFactory()
    theirs = ExperimentFactory()

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[theirs.id])
    assert not form.is_valid()


def test_initial_scope_follows_the_saved_allowlist():
    team = TeamFactory()
    assert TeamPublicKeyForm(instance=team).initial["export_scope"] == EXPORT_SCOPE_ALL

    team.exportable_experiments.add(ExperimentFactory(team=team))
    assert TeamPublicKeyForm(instance=team).initial["export_scope"] == EXPORT_SCOPE_SELECTED
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/teams/tests/test_forms.py -v`
Expected: FAIL with `ImportError: cannot import name 'EXPORT_SCOPE_ALL'`.

- [ ] **Step 3: Extend the form**

In `apps/teams/forms.py`, add the imports (`from apps.teams.export.selection import selectable_chatbots`) and:

```python
EXPORT_SCOPE_ALL = "all"
EXPORT_SCOPE_SELECTED = "selected"


class TeamPublicKeyForm(forms.ModelForm):
    export_scope = forms.ChoiceField(
        choices=(
            (EXPORT_SCOPE_ALL, _("All chatbots")),
            (EXPORT_SCOPE_SELECTED, _("Only selected chatbots")),
        ),
        widget=forms.RadioSelect,
        label=_("What may be exported"),
    )

    class Meta:
        model = Team
        fields = ("public_key", "is_migrating", "exportable_experiments")
        labels = {
            "public_key": _("Public Key"),
            "is_migrating": _("Migration mode"),
            "exportable_experiments": _("Chatbots"),
        }
        help_texts = {
            "public_key": _("Public key used to seal data exported from this team."),
            "is_migrating": _(
                "Freeze this team's outbound message firing while its data is migrated to another server."
            ),
            "exportable_experiments": _(
                "Selecting a chatbot includes all of its versions, including ones published later. "
                "Chatbots created after you save are not included."
            ),
        }
        widgets = {
            "public_key": forms.Textarea(attrs={"rows": 4, "placeholder": "-----BEGIN PUBLIC KEY-----"}),
            "exportable_experiments": forms.SelectMultiple(attrs={"class": "chatbot-allowlist"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        team = self.instance
        # The queryset is also the validation: a published version's id or another team's chatbot is
        # rejected rather than silently accepted.
        self.fields["exportable_experiments"].queryset = selectable_chatbots(team).order_by("name")
        self.fields["exportable_experiments"].required = False
        if "export_scope" not in self.initial:
            has_selection = team.pk is not None and team.exportable_experiments.exists()
            self.initial["export_scope"] = EXPORT_SCOPE_SELECTED if has_selection else EXPORT_SCOPE_ALL

    def clean_public_key(self):
        value = self.cleaned_data.get("public_key", "")
        if not value:
            return value
        try:
            load_pem_public_key(value.encode())
        except (ValueError, UnsupportedAlgorithm) as e:
            raise ValidationError(_("Enter a valid PEM-encoded public key.")) from e
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("export_scope") == EXPORT_SCOPE_ALL:
            cleaned["exportable_experiments"] = self.fields["exportable_experiments"].queryset.none()
        elif not cleaned.get("exportable_experiments"):
            self.add_error(
                "exportable_experiments",
                ValidationError(_("Pick at least one chatbot, or choose All chatbots.")),
            )
        return cleaned
```

- [ ] **Step 4: Run the form tests**

Run: `uv run pytest apps/teams/tests/test_forms.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing view test**

Append to `apps/teams/tests/test_manage_team_view.py`:

```python
def test_set_public_key_saves_the_allowlist(client):
    team = TeamWithUsersFactory()
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    chatbot = ExperimentFactory(team=team)
    client.force_login(admin)

    response = client.post(
        reverse("single_team:set_public_key", args=[team.slug]),
        {"public_key": "", "export_scope": "selected", "exportable_experiments": [chatbot.id], "is_migrating": "on"},
    )

    assert response.status_code == 200
    assert list(team.exportable_experiments.all()) == [chatbot]


def test_a_rejected_public_key_re_renders_the_submitted_selection(client):
    """ModelForm._post_clean writes the submitted values onto the instance in memory. The card reads
    the bound form, so a rejected key must not lose the admin's unsaved picks."""
    team = TeamWithUsersFactory()
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    chatbot = ExperimentFactory(team=team)
    client.force_login(admin)

    response = client.post(
        reverse("single_team:set_public_key", args=[team.slug]),
        {"public_key": "not a key", "export_scope": "selected", "exportable_experiments": [chatbot.id]},
    )

    assert response.status_code == 200
    form = response.context["public_key_form"]
    assert form.errors["public_key"]
    assert list(form.data.getlist("exportable_experiments")) == [str(chatbot.id)]
    assert list(team.exportable_experiments.all()) == []
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest apps/teams/tests/test_manage_team_view.py -k allowlist -v`
Expected: FAIL — the form rejects the post because `export_scope` is not a field yet, or the m2m is not saved.

- [ ] **Step 7: Save the m2m in the view**

In `apps/teams/views/manage_team_views.py`, `set_public_key` currently calls `form.save()`, which for a `ModelForm` with `commit=True` already writes m2m fields. Only the success message and the docstring change:

```python
@require_POST
@permission_required("teams.change_team", raise_exception=True)
def set_public_key(request, team_slug):
    """Saves the public key, the export scope and the migration-mode toggle together: they are set
    in one action on the Migration card, before a migration starts."""
    form = TeamPublicKeyForm(request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Migration settings saved."))
    else:
        messages.error(request, _("Could not save the migration settings."))
        # ModelForm.is_valid() has already written the submitted (rejected) values onto
        # request.team in memory via _post_clean(), even though nothing was saved. The
        # migration-mode checkbox and the scope radio below read the bound form, but the
        # card's badges read request.team, so refresh the instance from the database to undo
        # that in-memory mutation -- this doesn't touch form.errors.
        request.team.refresh_from_db()
    return render(
        request,
        "teams/manage_team.html",
        _manage_team_context(request, request.team, public_key_form=form),
    )
```

- [ ] **Step 8: Run the view tests**

Run: `uv run pytest apps/teams/tests/ -v`
Expected: PASS.

- [ ] **Step 9: Replace the card markup**

In `templates/teams/manage_team.html`, replace the `Migration public key` card (the second `app-card` inside `#data`) with:

```html
              <div class="app-card"
                   x-data="{
                     scope: '{{ public_key_form.export_scope.value|default:'all'|escapejs }}',
                     initialSelection: {{ saved_allowlist_ids|default:'[]' }},
                     selection: [],
                     get changedMidMigration() {
                       return {{ team.is_migrating|yesno:'true,false' }}
                         && JSON.stringify([...this.selection].sort()) !== JSON.stringify([...this.initialSelection].sort());
                     }
                   }"
                   x-init="
                     const picker = new window.TomSelect($refs.allowlist, {
                       plugins: ['remove_button'],
                       maxItems: null,
                       searchField: ['text'],
                       hideSelected: true,
                       closeAfterSelect: true,
                       onChange: (values) => { selection = (values || []).map(Number); }
                     });
                     selection = picker.getValue().map(Number);
                     $watch('scope', (value) => value === 'all' ? picker.disable() : picker.enable());
                     if (scope === 'all') { picker.disable(); }
                     $refs.selectAll.addEventListener('click', () => picker.setValue(Object.keys(picker.options)));
                     $refs.clearAll.addEventListener('click', () => picker.clear());
                   ">
                <div class="flex justify-between items-start">
                  <div>
                    <h3 class="pg-subtitle font-bold text-sm">{% translate "Migration" %}</h3>
                    <span class="text-neutral-500 section-subtitle">{% translate "Seals secret data exported from this team, and controls what may leave this server." %}</span>
                  </div>
                  <div class="flex gap-2">
                    {% if team.public_key %}
                      <span class="badge badge-success">{% translate "Key set" %}</span>
                    {% else %}
                      <span class="badge badge-warning">{% translate "Key not set" %}</span>
                    {% endif %}
                    {% if team.is_migrating %}
                      <span class="badge badge-info">{% translate "Migration mode on" %}</span>
                    {% endif %}
                  </div>
                </div>

                <form method="post" action="{% url 'single_team:set_public_key' request.team.slug %}" class="mt-2">
                  {% csrf_token %}
                  {{ public_key_form.non_field_errors }}
                  {% render_form_fields public_key_form "public_key" %}

                  <fieldset class="mt-4">
                    <legend class="section-subtitle font-bold">{% translate "What may be exported" %}</legend>
                    {% for radio in public_key_form.export_scope %}
                      <label class="label cursor-pointer justify-start gap-2">
                        <input type="radio" name="export_scope" class="radio radio-sm"
                               value="{{ radio.data.value }}" x-model="scope">
                        <span class="section-subtitle">{{ radio.choice_label }}</span>
                      </label>
                    {% endfor %}
                  </fieldset>

                  <div class="mt-2" x-show="scope === 'selected'" x-cloak>
                    {{ public_key_form.exportable_experiments.errors }}
                    <select x-ref="allowlist" name="exportable_experiments" multiple
                            class="chatbot-allowlist w-full">
                      {% for chatbot in public_key_form.fields.exportable_experiments.queryset %}
                        <option value="{{ chatbot.id }}"
                                {% if chatbot.id|stringformat:"s" in submitted_allowlist_ids %}selected{% endif %}>
                          {{ chatbot.name }}{% if chatbot.is_archived %} {% translate "(archived)" %}{% endif %}
                        </option>
                      {% endfor %}
                    </select>
                    <p class="text-neutral-500 section-subtitle whitespace-normal mt-1">
                      {% translate "Selecting a chatbot includes all of its versions, including ones published later. Chatbots created after you save are not included." %}
                    </p>
                    <div class="pg-inline-buttons mt-2">
                      <button type="button" class="btn btn-xs btn-outline" x-ref="selectAll">{% translate "Select all" %}</button>
                      <button type="button" class="btn btn-xs btn-outline" x-ref="clearAll">{% translate "Clear" %}</button>
                    </div>
                  </div>

                  <div class="alert alert-warning mt-3" x-show="changedMidMigration" x-cloak>
                    <span class="section-subtitle whitespace-normal">
                      {% translate "A migration is in progress. Changing this makes the sync on the other server restart every resource from the beginning, and removing a chatbot does not delete what it has already copied." %}
                    </span>
                  </div>

                  <div class="flex flex-wrap items-center justify-between gap-2 mt-3">
                    <div class="min-w-0">
                      <label class="label cursor-pointer gap-2">
                        <input type="checkbox" name="is_migrating" class="checkbox checkbox-sm shrink-0"
                                {% if request.team.is_migrating %}checked{% endif %}>
                        <span class="section-subtitle">{% translate "Migration mode" %}</span>
                      </label>
                      <p class="text-neutral-500 section-subtitle whitespace-normal">
                        {% translate "While enabled, scheduled messages and event triggers (including timeout triggers) for this team are paused and will not fire." %}
                      </p>
                    </div>
                    <input class="btn btn-primary" type="submit" value="{% translate "Save" %}">
                  </div>
                </form>
              </div>
```

*Select all* picks every current chatbot explicitly; it does not switch the radio to *All chatbots*, so the "created after you save" sentence stays visible.

The migration-mode help text stays team-wide in both cases until Task 17 narrows the freeze.

- [ ] **Step 10: Supply the two template values from the view**

In `apps/teams/views/manage_team_views.py`, `_manage_team_context` (`:50`) — add below the `public_key_form` entry:

```python
    public_key_form = public_key_form or TeamPublicKeyForm(instance=team)
    saved_allowlist_ids = list(team.exportable_experiments.values_list("id", flat=True))
    return {
        ...
        "public_key_form": public_key_form,
        # The Alpine mid-migration warning compares against what is saved, not what is bound.
        "saved_allowlist_ids": json.dumps(saved_allowlist_ids),
        # Which options to mark selected: the bound values on a rejected submit, the saved ones
        # otherwise, so a validation error doesn't lose the admin's picks.
        "submitted_allowlist_ids": (
            public_key_form.data.getlist("exportable_experiments")
            if public_key_form.is_bound
            else [str(pk) for pk in saved_allowlist_ids]
        ),
        ...
    }
```

with `import json` at the top of the module, and the existing `public_key_form or ...` line removed from the dict literal since it is now computed above it.

- [ ] **Step 11: Drive it in the browser**

```bash
uv run inv runserver
```

Open `/a/<team-slug>/team/#data` as a team admin and confirm: the radio defaults to *All chatbots* with the picker hidden; switching to *Only selected* reveals a searchable multi-select of working chatbots; *Select all* fills it without moving the radio; saving with nothing picked shows the field error; saving a selection persists it across a reload; with migration mode saved on, changing the picks shows the warning and reverting them hides it again.

- [ ] **Step 12: Commit**

```bash
uv run inv ruff --paths apps/teams
pnpm run lint assets/javascript/site.js
git add apps/teams templates/teams/manage_team.html
git commit -m "feat: pick which chatbots a team may export on the migration card"
```

---

### Task 10: Show the selection on the app banner

`templates/teams/migration_lock_banner.html` says the whole team is migrating. With a selection it should say how many chatbots are, and link to the card. A count, not names, so the banner costs one `COUNT` per page and nothing unbounded.

**Files:**
- Modify: `templates/teams/migration_lock_banner.html`
- Modify: `templates/web/app/app_base.html:32-34`
- Test: `apps/teams/tests/test_migration_lock.py` (already holds the migration-mode view and audit tests, including `_team_with_admin()`)

**Interfaces:**
- Consumes: `request.team.exportable_experiments` (the template reads `.count` directly).
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/tests/test_migration_lock.py`:

```python
def test_banner_names_the_number_of_migrating_chatbots(client):
    team = TeamWithUsersFactory(is_migrating=True)
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    team.exportable_experiments.add(ExperimentFactory(team=team), ExperimentFactory(team=team))
    client.force_login(admin)

    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert "2 chatbots in this team are being migrated" in response.content.decode()


def test_banner_stays_team_wide_without_a_selection(client):
    team = TeamWithUsersFactory(is_migrating=True)
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    client.force_login(admin)

    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert "This team is undergoing a migration" in response.content.decode()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest apps/teams/tests/test_migration_lock.py -k banner -v`
Expected: FAIL — the banner text is unconditional.

- [ ] **Step 3: Branch the banner**

Replace `templates/teams/migration_lock_banner.html`:

```html
{% load i18n %}
<div class="alert alert-warning rounded-none justify-center text-center">
  {% with count=request.team.exportable_experiments.count %}
    {% if count %}
      {% blocktranslate count counter=count %}{{ counter }} chatbot in this team is being migrated. Do not edit it until the migration is complete.{% plural %}{{ counter }} chatbots in this team are being migrated. Do not edit them until the migration is complete.{% endblocktranslate %}
      <a class="link ml-1" href="{% url 'single_team:manage_team' request.team.slug %}#data">{% translate "Details" %}</a>
    {% else %}
      {% translate "This team is undergoing a migration. Do not create or edit chatbots until the migration is complete." %}
    {% endif %}
  {% endwith %}
</div>
```

Chatbot pages are unchanged in this iteration; a per-chatbot badge is a follow-up if the team-wide banner turns out to be too vague.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/teams/migration_lock_banner.html apps/teams/tests/test_migration_lock.py
git commit -m "feat: say how many chatbots are migrating on the app banner"
```

---

## Phase 3 — the scope

Manifest models fall into three classes under a chatbot selection:

- **(a) Owned** — filter by a path to the experiment family.
- **(b) Referenced** — a reverse closure from what the chatbot uses, not a path.
- **(c) Excluded** — `evaluations.*`, `human_annotations.*`, `analysis.*`. These must serve *empty* while a selection is active, or an eval row arrives referencing an experiment that was never synced and `resolve_fk` raises.

---

### Task 11: `ChatbotScope` — resolve one request's row sets

The scope object is built once per request and answers "which rows of model X". Bounded sets (the experiment family and the shared resources it reaches) resolve to id lists, so every dependent filter becomes `<column> IN (...)` against an indexed FK column, which the planner estimates well. The session-, chat-, message- and file-sized sets stay querysets — `pk__in` on a materialised list does not scale for a busy bot.

This task builds the sets and their tests. Task 12 wires them to models.

**Files:**
- Create: `apps/teams/export/chatbot_scope.py`
- Test: `apps/teams/export/tests/test_chatbot_scope.py`

**Interfaces:**
- Consumes: `apps.teams.export.selection.selected_experiment_ids(team)`, `apps.teams.export.selection.expand_to_family(ids)`.
- Produces:
  - `build_scope(team) -> ChatbotScope | None` — `None` when the team's allowlist is empty (export everything).
  - `ChatbotScope` with these members, all cached per instance:
    - id lists: `experiment_ids`, `channel_ids`, `pipeline_ids`, `node_ids`, `collection_ids`, `custom_action_ids`, `source_material_ids`, `consent_form_ids`, `synthetic_voice_ids`, `llm_provider_ids`, `llm_provider_model_ids`, `embedding_provider_model_ids`, `voice_provider_ids`, `messaging_provider_ids`, `auth_provider_ids`, `trace_provider_ids`
    - pk querysets: `sessions`, `chats`, `chat_messages`, `participants`, `files`

- [ ] **Step 1: Write the failing test**

Create `apps/teams/export/tests/test_chatbot_scope.py`:

```python
"""The row sets one chatbot selection resolves to. Each test builds two chatbots and asserts the
other one's rows stay out."""

import pytest

from apps.teams.export.chatbot_scope import ChatbotScope, build_scope
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.chat import ChatFactory, ChatMessageFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import (
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def test_build_scope_is_none_without_a_selection():
    assert build_scope(TeamFactory()) is None


def test_build_scope_covers_the_selected_family():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)
    other = ExperimentFactory(team=team)
    team.exportable_experiments.add(working)

    scope = build_scope(team)

    assert set(scope.experiment_ids) == {working.id, published.id}
    assert other.id not in scope.experiment_ids


def test_sessions_chats_and_messages_follow_the_family():
    team = TeamFactory()
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)
    my_session = ExperimentSessionFactory(experiment=mine, team=team)
    their_session = ExperimentSessionFactory(experiment=theirs, team=team)
    my_message = ChatMessageFactory(chat=my_session.chat)
    their_message = ChatMessageFactory(chat=their_session.chat)

    scope = build_scope(team)

    assert set(scope.sessions.values_list("pk", flat=True)) == {my_session.id}
    assert set(scope.chats.values_list("pk", flat=True)) == {my_session.chat_id}
    assert set(scope.chat_messages.values_list("pk", flat=True)) == {my_message.id}
    assert their_message.id not in set(scope.chat_messages.values_list("pk", flat=True))


def test_participants_include_ones_reachable_only_through_participant_data():
    """ParticipantData.participant is non-null, so a participant with data for the chatbot but no
    session must still be exported or resolve_fk raises on import."""
    from apps.utils.factories.experiment import ParticipantDataFactory, ParticipantFactory

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    with_session = ExperimentSessionFactory(experiment=chatbot, team=team).participant
    data_only = ParticipantFactory(team=team)
    ParticipantDataFactory(team=team, experiment=chatbot, participant=data_only)
    unrelated = ParticipantFactory(team=team)

    ids = set(build_scope(team).participants.values_list("pk", flat=True))

    assert {with_session.id, data_only.id} <= ids
    assert unrelated.id not in ids


def test_channels_cover_the_chatbots_own_and_the_teams_shared_ones():
    team = TeamFactory()
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)
    my_channel = ExperimentChannelFactory(team=team, experiment=mine)
    their_channel = ExperimentChannelFactory(team=team, experiment=theirs)
    shared = ExperimentChannelFactory(team=team, experiment=None)

    ids = set(build_scope(team).channel_ids)

    assert {my_channel.id, shared.id} <= ids
    assert their_channel.id not in ids


def test_pipeline_and_node_closure_follows_the_chatbots_pipeline():
    team = TeamFactory()
    pipeline = PipelineFactory(team=team)
    node = NodeFactory(pipeline=pipeline)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    other_pipeline = PipelineFactory(team=team)
    ExperimentFactory(team=team, pipeline=other_pipeline)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert pipeline.id in scope.pipeline_ids
    assert other_pipeline.id not in scope.pipeline_ids
    assert node.id in scope.node_ids


def test_pipeline_closure_includes_one_named_in_an_event_action():
    from apps.utils.factories.events import StaticTriggerFactory

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    started = PipelineFactory(team=team)
    trigger = StaticTriggerFactory(experiment=chatbot)
    trigger.action.action_type = "pipeline_start"
    trigger.action.params = {"pipeline_id": started.id}
    trigger.action.save()

    assert started.id in build_scope(team).pipeline_ids


def test_referenced_resources_include_their_working_versions():
    """A published resource carries a self-referential working_version FK; exporting it without its
    working row leaves a link the target cannot resolve."""
    team = TeamFactory()
    working_pipeline = PipelineFactory(team=team)
    published_pipeline = PipelineFactory(team=team, working_version=working_pipeline)
    chatbot = ExperimentFactory(team=team, pipeline=published_pipeline)
    team.exportable_experiments.add(chatbot)

    assert {working_pipeline.id, published_pipeline.id} <= set(build_scope(team).pipeline_ids)


def test_provider_closure_follows_the_nodes_and_the_chatbot():
    team = TeamFactory()
    provider = LlmProviderFactory(team=team)
    unused = LlmProviderFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, llm_provider=provider)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    team.exportable_experiments.add(chatbot)

    ids = build_scope(team).llm_provider_ids

    assert provider.id in ids
    assert unused.id not in ids


def test_collection_and_file_closure_follows_the_nodes():
    from apps.utils.factories.documents import CollectionFileFactory
    from apps.utils.factories.files import FileFactory

    team = TeamFactory()
    collection = CollectionFactory(team=team, llm_provider=None, embedding_provider_model=None)
    used_file = FileFactory(team=team)
    CollectionFileFactory(collection=collection, file=used_file)
    unused_file = FileFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, collection=collection)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert collection.id in scope.collection_ids
    file_ids = set(scope.files.values_list("pk", flat=True))
    assert used_file.id in file_ids
    assert unused_file.id not in file_ids


def test_file_closure_includes_chat_attachments():
    from apps.utils.factories.files import FileFactory

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    session = ExperimentSessionFactory(experiment=chatbot, team=team)
    attachment = session.chat.attachments.create(tool_type="code_interpreter")
    attached = FileFactory(team=team)
    attachment.files.add(attached)

    assert attached.id in set(build_scope(team).files.values_list("pk", flat=True))


def test_consent_form_and_source_material_follow_the_chatbot():
    team = TeamFactory()
    consent = ConsentFormFactory(team=team)
    material = SourceMaterialFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, source_material=material)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline, consent_form=consent)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert consent.id in scope.consent_form_ids
    assert material.id in scope.source_material_ids


def test_scope_sets_are_computed_once(django_assert_num_queries):
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    scope = build_scope(team)

    scope.experiment_ids
    with django_assert_num_queries(0):
        scope.experiment_ids
```

Adjust factory keyword names to the ones the repo's factories actually take; run one test first and fix the call sites before writing the rest.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest apps/teams/export/tests/test_chatbot_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.teams.export.chatbot_scope`.

- [ ] **Step 3: Write the scope module**

Create `apps/teams/export/chatbot_scope.py`:

```python
"""Which rows a chatbot-scoped export may serve.

One ``ChatbotScope`` is built per request and answers "which rows of model X" for every resource.
Bounded sets resolve to id lists, so a dependent filter is an indexed ``IN`` the planner estimates
well; the session-, chat-, message- and file-sized sets stay querysets, because ``pk__in`` on a
materialised list does not scale for a busy chatbot.
"""

from collections.abc import Sequence
from functools import cached_property

from django.apps import apps
from django.db.models import Model, Q, QuerySet

from apps.teams.export.selection import expand_to_family, selected_experiment_ids


def _model(label: str) -> type[Model]:
    return apps.get_model(*label.split("."))


def _with_working_versions(model: type[Model], base: Q) -> Q:
    """``base``, plus the working version of every row it matches.

    A published row's ``working_version`` FK points at a row that must be exported too, or the target
    cannot resolve it. Working versions have the lower pk, so the manifest's pk ordering still serves
    them first.
    """
    return base | Q(pk__in=model._base_manager.filter(base).values("working_version_id"))


def _resolve(label: str, base: Q, *, versioned: bool = True) -> list[int]:
    """A bounded set as a sorted id list."""
    model = _model(label)
    if versioned:
        base = _with_working_versions(model, base)
    return sorted(model._base_manager.filter(base).values_list("pk", flat=True))


def _ids_from(label: str, base: Q, column: str) -> QuerySet:
    """A column of one model as a subquery, for use in ``pk__in=``."""
    return _model(label)._base_manager.filter(base).values(column)


class ChatbotScope:
    """The rows one team's chatbot allowlist may export."""

    def __init__(self, team, selected_ids: Sequence[int]):
        self.team = team
        self.selected_ids = list(selected_ids)

    # --- class (a): owned by the chatbot ------------------------------------------------------

    @cached_property
    def experiment_ids(self) -> list[int]:
        """The selected chatbots and every version of each: published ones and archived ones."""
        return sorted(expand_to_family(self.selected_ids).values_list("pk", flat=True))

    @cached_property
    def sessions(self) -> QuerySet:
        return _ids_from("experiments.experimentsession", Q(experiment_id__in=self.experiment_ids), "pk")

    @cached_property
    def chats(self) -> QuerySet:
        # Through the session's forward FK rather than Chat's reverse OneToOne: a forward chain is a
        # clean semi-join, so the limit still pushes down.
        return _ids_from("experiments.experimentsession", Q(experiment_id__in=self.experiment_ids), "chat_id")

    @cached_property
    def chat_messages(self) -> QuerySet:
        return _ids_from("chat.chatmessage", Q(chat_id__in=self.chats), "pk")

    @cached_property
    def participants(self) -> QuerySet:
        """Participants reachable from the family. Three branches because ``ParticipantData`` and
        ``ScheduledMessage`` both hold a non-null participant FK and are scoped by experiment, so a
        participant reachable only through one of those still has to be exported."""
        family = self.experiment_ids
        return _ids_from(
            "experiments.participant",
            Q(pk__in=_ids_from("experiments.experimentsession", Q(experiment_id__in=family), "participant_id"))
            | Q(pk__in=_ids_from("experiments.participantdata", Q(experiment_id__in=family), "participant_id"))
            | Q(pk__in=_ids_from("events.scheduledmessage", Q(experiment_id__in=family), "participant_id")),
            "pk",
        )

    @cached_property
    def channel_ids(self) -> list[int]:
        """The family's own channels plus the team-level ones (web, API, evaluations, widget), which
        carry no experiment. A session pointing at another chatbot's channel -- the ``is_stale()``
        case -- loses the link; the FK is nullable, so it nulls rather than failing the import."""
        return _resolve(
            "bot_channels.experimentchannel",
            Q(experiment_id__in=self.experiment_ids) | Q(experiment__isnull=True),
            versioned=False,
        )

    # --- class (b): reached by what the chatbot uses -------------------------------------------

    @cached_property
    def pipeline_ids(self) -> list[int]:
        return _resolve(
            "pipelines.pipeline",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "pipeline_id"))
            | Q(pk__in=self._event_action_pipeline_ids),
        )

    @cached_property
    def _event_action_pipeline_ids(self) -> list[int]:
        """Pipelines a ``pipeline_start`` event action names in its params. Which params carry an id
        is declared in ``apps.events.versioning``; read in Python because the set is bounded by the
        family's trigger count and a JSON scalar needs casting either way."""
        from apps.events.versioning import get_event_action_param_specs  # noqa: PLC0415 - models load lazily

        family = self.experiment_ids
        actions = _model("events.eventaction")._base_manager.filter(
            Q(static_trigger__experiment_id__in=family)
            | Q(timeout_trigger__experiment_id__in=family)
            | Q(scheduled_trigger__experiment_id__in=family)
        )
        ids = []
        for action_type, params in actions.values_list("action_type", "params"):
            for spec in get_event_action_param_specs(action_type):
                if spec.model_label.lower() != "pipelines.pipeline":
                    continue
                value = (params or {}).get(spec.param_name)
                if value:
                    ids.append(int(value))
        return ids

    @cached_property
    def node_ids(self) -> list[int]:
        return _resolve("pipelines.node", Q(pipeline_id__in=self.pipeline_ids))

    @cached_property
    def collection_ids(self) -> list[int]:
        node = _model("pipelines.node")
        return _resolve(
            "documents.collection",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "collection_id"))
            | Q(
                pk__in=node.collection_indexes.through.objects.filter(node_id__in=self.node_ids).values("collection_id")
            ),
        )

    @cached_property
    def files(self) -> QuerySet:
        """Files the chatbot reaches: its collections' files (``Collection.files`` and
        ``DocumentSource.files`` are both m2m through ``CollectionFile``, so one branch covers both),
        its chat attachments, and any synthetic voice sample. Stays a queryset -- the attachment
        branch grows with conversation volume."""
        base = (
            Q(pk__in=_ids_from("documents.collectionfile", Q(collection_id__in=self.collection_ids), "file_id"))
            | Q(chatattachment__chat_id__in=self.chats)
            | Q(pk__in=_ids_from("experiments.syntheticvoice", Q(pk__in=self.synthetic_voice_ids), "file_id"))
        )
        file_model = _model("files.file")
        return file_model._base_manager.filter(_with_working_versions(file_model, base)).values("pk")

    @cached_property
    def custom_action_ids(self) -> list[int]:
        return _resolve(
            "custom_actions.customaction",
            Q(
                pk__in=_ids_from(
                    "custom_actions.customactionoperation", Q(node_id__in=self.node_ids), "custom_action_id"
                )
            ),
            versioned=False,
        )

    @cached_property
    def source_material_ids(self) -> list[int]:
        return _resolve(
            "experiments.sourcematerial",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "source_material_id")),
        )

    @cached_property
    def consent_form_ids(self) -> list[int]:
        return _resolve(
            "experiments.consentform",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "consent_form_id")),
        )

    @cached_property
    def synthetic_voice_ids(self) -> list[int]:
        return _resolve(
            "experiments.syntheticvoice",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "synthetic_voice_id"))
            | Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "synthetic_voice_id")),
            versioned=False,
        )

    @cached_property
    def llm_provider_ids(self) -> list[int]:
        nodes = Q(pk__in=self.node_ids)
        collections = Q(pk__in=self.collection_ids)
        return _resolve(
            "service_providers.llmprovider",
            Q(pk__in=_ids_from("pipelines.node", nodes, "llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "contextualizer_llm_provider_id"))
            | Q(pk__in=_ids_from("documents.collection", collections, "reranker_provider_id")),
            versioned=False,
        )

    @cached_property
    def llm_provider_model_ids(self) -> list[int]:
        return _resolve(
            "service_providers.llmprovidermodel",
            Q(pk__in=_ids_from("pipelines.node", Q(pk__in=self.node_ids), "llm_provider_model_id"))
            | Q(pk__in=_ids_from("documents.collection", Q(pk__in=self.collection_ids), "contextualizer_llm_model_id")),
            versioned=False,
        )

    @cached_property
    def embedding_provider_model_ids(self) -> list[int]:
        return _resolve(
            "service_providers.embeddingprovidermodel",
            Q(pk__in=_ids_from("documents.collection", Q(pk__in=self.collection_ids), "embedding_provider_model_id")),
            versioned=False,
        )

    @cached_property
    def voice_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.voiceprovider",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "voice_provider_id"))
            | Q(
                pk__in=_ids_from("experiments.syntheticvoice", Q(pk__in=self.synthetic_voice_ids), "voice_provider_id")
            ),
            versioned=False,
        )

    @cached_property
    def messaging_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.messagingprovider",
            Q(pk__in=_ids_from("bot_channels.experimentchannel", Q(pk__in=self.channel_ids), "messaging_provider_id")),
            versioned=False,
        )

    @cached_property
    def auth_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.authprovider",
            Q(pk__in=_ids_from("custom_actions.customaction", Q(pk__in=self.custom_action_ids), "auth_provider_id"))
            | Q(
                pk__in=_ids_from(
                    "documents.documentsource", Q(collection_id__in=self.collection_ids), "auth_provider_id"
                )
            ),
            versioned=False,
        )

    @cached_property
    def trace_provider_ids(self) -> list[int]:
        return _resolve(
            "service_providers.traceprovider",
            Q(pk__in=_ids_from("experiments.experiment", Q(pk__in=self.experiment_ids), "trace_provider_id")),
            versioned=False,
        )


def build_scope(team) -> ChatbotScope | None:
    """The scope for this team's export, or None when it exports everything.

    An empty allowlist means the whole team, so a chatbot created after the selection was saved is
    exportable exactly when no selection is active.
    """
    selected = selected_experiment_ids(team)
    return ChatbotScope(team, selected) if selected else None
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_chatbot_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/export
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export/chatbot_scope.py apps/teams/export/tests/test_chatbot_scope.py
git commit -m "feat: resolve the row sets a chatbot selection may export"
```

---

### Task 12: The per-model scope registry and `scoped_queryset`

One entry per manifest model, hand-written and reviewable in a diff. A partition test forces a decision when a model is added, the same way `test_every_first_party_model_is_synced_or_ignored` does today.

Two rules the registry follows throughout:

- **Forward FK chains as paths, everything else as `pk__in=<subquery>`.** `SELECT DISTINCT … ORDER BY updated_at, id LIMIT 101` cannot stop early — the whole join is deduplicated before the limit applies, on every page. A semi-join has no duplicates, needs no `DISTINCT`, and lets the limit push down.
- **The generic-FK filters are built from the same querysets that scope `chats`, `chat_messages` and `sessions`**, never from an independently written path. `_resolve_generic_fks` (`apps/teams/export/importer.py:391`) raises rather than nulling, so a filter even slightly wider than what was synced aborts the import mid-run.

**Files:**
- Modify: `apps/teams/export/chatbot_scope.py` (append the registry)
- Modify: `apps/teams/export/manifest.py:243-269`
- Test: `apps/teams/export/tests/test_chatbot_scope.py`, `apps/teams/export/tests/test_manifest.py`

**Interfaces:**
- Consumes: `ChatbotScope` from Task 11; `manifest.team_scoped_queryset(entry, team)`.
- Produces:
  - `ScopeClass` — a `StrEnum` with `OWNED = "owned"`, `REFERENCED = "referenced"`, `EXCLUDED = "excluded"`.
  - `ScopeRule` — frozen dataclass with `scope_class: ScopeClass` and `build_q: Callable[[ChatbotScope], Q] | None`.
  - `CHATBOT_SCOPE_REGISTRY: dict[str, ScopeRule]` — one entry per manifest model.
  - `scope_class_for(model_label: str) -> ScopeClass`.
  - `manifest.scoped_queryset(entry: ManifestEntry, team, scope: ChatbotScope | None = None) -> QuerySet`.

- [ ] **Step 1: Write the failing partition test**

Append to `apps/teams/export/tests/test_chatbot_scope.py`:

```python
def test_every_manifest_model_has_a_scope_rule():
    """A model added to the manifest must be classified, or a chatbot-scoped sync silently serves it
    team-wide and drags in another chatbot's rows."""
    from apps.teams.export import manifest
    from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY

    unclassified = {e.model for e in manifest.MANIFEST_ENTRIES} - set(CHATBOT_SCOPE_REGISTRY)
    assert not unclassified, "Add these to CHATBOT_SCOPE_REGISTRY: " + ", ".join(sorted(unclassified))


def test_scope_registry_has_no_entries_for_unsynced_models():
    from apps.teams.export import manifest
    from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY

    assert set(CHATBOT_SCOPE_REGISTRY) <= {e.model for e in manifest.MANIFEST_ENTRIES}


def test_excluded_rules_carry_no_query_and_the_others_do():
    from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY, ScopeClass

    for label, rule in CHATBOT_SCOPE_REGISTRY.items():
        if rule.scope_class is ScopeClass.EXCLUDED:
            assert rule.build_q is None, label
        else:
            assert rule.build_q is not None, label


def test_the_excluded_classes_are_exactly_the_brief():
    from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY, ScopeClass

    excluded = {l for l, r in CHATBOT_SCOPE_REGISTRY.items() if r.scope_class is ScopeClass.EXCLUDED}
    assert {label.split(".")[0] for label in excluded} == {"evaluations", "human_annotations", "analysis"}
```

Append to `apps/teams/export/tests/test_manifest.py`:

```python
@pytest.mark.django_db()
def test_scoped_queryset_without_a_scope_matches_the_team_queryset():
    team = TeamFactory()
    ExperimentFactory(team=team)
    entry = manifest.get_manifest_entry("chatbots")
    assert set(manifest.scoped_queryset(entry, team).values_list("pk", flat=True)) == set(
        manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True)
    )


@pytest.mark.django_db()
def test_scoped_queryset_keeps_only_the_selected_family():
    from apps.teams.export.chatbot_scope import build_scope

    team = TeamFactory()
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)

    entry = manifest.get_manifest_entry("chatbots")
    pks = set(manifest.scoped_queryset(entry, team, build_scope(team)).values_list("pk", flat=True))

    assert pks == {mine.id}
    assert theirs.id not in pks


@pytest.mark.django_db()
def test_scoped_queryset_serves_an_excluded_resource_empty():
    """An evaluation row referencing an experiment that was never synced makes resolve_fk raise
    mid-import, so these resources have to come back empty rather than team-wide."""
    from apps.teams.export.chatbot_scope import build_scope
    from apps.utils.factories.evaluations import EvaluatorFactory

    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team))
    EvaluatorFactory(team=team)

    entry = manifest.get_manifest_entry("evaluators")
    assert not manifest.scoped_queryset(entry, team, build_scope(team)).exists()


@pytest.mark.django_db()
def test_scoped_queryset_narrows_the_global_rows_it_serves():
    """Globals are matched by natural key on the target and never created, so serving one the target
    lacks aborts the import with MissingGlobalRow. Under a scope only referenced globals go out."""
    from apps.teams.export.chatbot_scope import build_scope

    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team))
    unreferenced_global = LlmProviderModelFactory(team=None)

    entry = manifest.get_manifest_entry("llm_provider_models")
    pks = set(manifest.scoped_queryset(entry, team, build_scope(team)).values_list("pk", flat=True))

    assert unreferenced_global.pk not in pks
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/teams/export/tests/ -k "scope_rule or scoped_queryset or excluded_rules or excluded_classes" -v`
Expected: FAIL with `ImportError: cannot import name 'CHATBOT_SCOPE_REGISTRY'`.

- [ ] **Step 3: Append the registry to `chatbot_scope.py`**

```python
class ScopeClass(StrEnum):
    """How a model is filtered when a chatbot selection is active."""

    OWNED = "owned"
    """Belongs to a chatbot: filtered by a path to the experiment family."""

    REFERENCED = "referenced"
    """Shared across the team: kept when the selected chatbots reach it."""

    EXCLUDED = "excluded"
    """Served empty. The client skips these resources entirely."""


@dataclass(frozen=True)
class ScopeRule:
    scope_class: ScopeClass
    build_q: Callable[[ChatbotScope], Q] | None = None


def _owned(build_q: Callable[[ChatbotScope], Q]) -> ScopeRule:
    return ScopeRule(ScopeClass.OWNED, build_q)


def _referenced(build_q: Callable[[ChatbotScope], Q]) -> ScopeRule:
    return ScopeRule(ScopeClass.REFERENCED, build_q)


def _excluded() -> ScopeRule:
    return ScopeRule(ScopeClass.EXCLUDED)


def _team_wide() -> ScopeRule:
    """Kept whole. Used where narrowing buys nothing and only adds a way for a row's FK to fail."""
    return ScopeRule(ScopeClass.REFERENCED, lambda _scope: Q())


def _generic_target_q(scope: ChatbotScope, ct_field: str, id_field: str) -> Q:
    """A generic-FK row's scope, built from the very querysets that scope chats, messages and
    sessions. ``_resolve_generic_fks`` raises rather than nulling, so a filter even slightly wider
    than what was synced aborts the import mid-run; deriving both from one place makes that drift
    impossible by construction."""
    content_type = apps.get_model("contenttypes", "ContentType").objects
    pairs = (
        (content_type.get_by_natural_key("chat", "chat"), scope.chats),
        (content_type.get_by_natural_key("chat", "chatmessage"), scope.chat_messages),
        (content_type.get_by_natural_key("experiments", "experimentsession"), scope.sessions),
    )
    query = Q(pk__in=[])
    for ct, targets in pairs:
        query |= Q(**{ct_field: ct, f"{id_field}__in": targets})
    return query


# One rule per manifest model. Hand-written: the shortest path is not always the right one, and
# several cross nullable FKs where "no path" and "path to null" mean different things.
CHATBOT_SCOPE_REGISTRY: dict[str, ScopeRule] = {
    # --- (a) owned by the chatbot ---------------------------------------------------------------
    "experiments.experiment": _owned(lambda s: Q(pk__in=s.experiment_ids)),
    "bot_channels.experimentchannel": _owned(lambda s: Q(pk__in=s.channel_ids)),
    "events.statictrigger": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "events.timeouttrigger": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "events.scheduledtrigger": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    # Each trigger type holds a OneToOne to its action, so these are single-valued joins: no
    # duplicates and no DISTINCT. A ScheduledMessage's action is its scheduled trigger's.
    "events.eventaction": _owned(
        lambda s: (
            Q(static_trigger__experiment_id__in=s.experiment_ids)
            | Q(timeout_trigger__experiment_id__in=s.experiment_ids)
            | Q(scheduled_trigger__experiment_id__in=s.experiment_ids)
        )
    ),
    "events.scheduledmessage": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "experiments.experimentsession": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "experiments.participantdata": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    # Shared between chatbots: a participant synced for one is reused by the next. That is what
    # makes a single FK translation store per team necessary.
    "experiments.participant": _owned(lambda s: Q(pk__in=s.participants)),
    "chat.chat": _owned(lambda s: Q(pk__in=s.chats)),
    "chat.chatmessage": _owned(lambda s: Q(chat_id__in=s.chats)),
    "chat.chatattachment": _owned(lambda s: Q(chat_id__in=s.chats)),
    "trace.trace": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "pipelines.pipelinechathistory": _owned(lambda s: Q(session_id__in=s.sessions)),
    "pipelines.pipelinechatmessages": _owned(lambda s: Q(chat_history__session_id__in=s.sessions)),
    "cost_tracking.usagerecord": _owned(lambda s: Q(experiment_id__in=s.experiment_ids)),
    "annotations.customtaggeditem": _owned(lambda s: _generic_target_q(s, "content_type", "object_id")),
    "annotations.usercomment": _owned(lambda s: _generic_target_q(s, "content_type", "object_id")),
    # Session scores only. A score hanging off an evaluation result or a human annotation belongs to
    # an excluded model; its FK is nullable, so keeping the row would silently drop its provenance.
    "assessments.score": _owned(
        lambda s: (
            _generic_target_q(s, "target_content_type", "target_object_id")
            & Q(automated_result__isnull=True, review__isnull=True)
        )
    ),
    # --- (b) reached by what the chatbot uses ----------------------------------------------------
    "pipelines.pipeline": _referenced(lambda s: Q(pk__in=s.pipeline_ids)),
    "pipelines.node": _referenced(lambda s: Q(pipeline_id__in=s.pipeline_ids)),
    "documents.collection": _referenced(lambda s: Q(pk__in=s.collection_ids)),
    "documents.collectionfile": _referenced(lambda s: Q(collection_id__in=s.collection_ids)),
    "documents.documentsource": _referenced(lambda s: Q(collection_id__in=s.collection_ids)),
    "files.filechunkembedding": _referenced(lambda s: Q(collection_id__in=s.collection_ids)),
    "files.file": _referenced(lambda s: Q(pk__in=s.files)),
    "custom_actions.customaction": _referenced(lambda s: Q(pk__in=s.custom_action_ids)),
    "custom_actions.customactionoperation": _referenced(lambda s: Q(node_id__in=s.node_ids)),
    "experiments.sourcematerial": _referenced(lambda s: Q(pk__in=s.source_material_ids)),
    "experiments.consentform": _referenced(lambda s: Q(pk__in=s.consent_form_ids)),
    "experiments.syntheticvoice": _referenced(lambda s: Q(pk__in=s.synthetic_voice_ids)),
    "service_providers.llmprovider": _referenced(lambda s: Q(pk__in=s.llm_provider_ids)),
    "service_providers.llmprovidermodel": _referenced(lambda s: Q(pk__in=s.llm_provider_model_ids)),
    "service_providers.embeddingprovidermodel": _referenced(lambda s: Q(pk__in=s.embedding_provider_model_ids)),
    "service_providers.voiceprovider": _referenced(lambda s: Q(pk__in=s.voice_provider_ids)),
    "service_providers.messagingprovider": _referenced(lambda s: Q(pk__in=s.messaging_provider_ids)),
    "service_providers.authprovider": _referenced(lambda s: Q(pk__in=s.auth_provider_ids)),
    "service_providers.traceprovider": _referenced(lambda s: Q(pk__in=s.trace_provider_ids)),
    # Team-wide and small. Narrowing tags would only add a way for a tagged item's non-null tag FK
    # to fail; several FKs to a user are non-null and the row is cheap.
    "annotations.tag": _team_wide(),
    "users.customuser": _team_wide(),
    "cost_tracking.pricingrule": _team_wide(),
    "ocs_notifications.eventtype": _team_wide(),
    "ocs_notifications.usernotificationpreferences": _team_wide(),
    "ocs_notifications.notificationevent": _team_wide(),
    "ocs_notifications.eventuser": _team_wide(),
    # --- (c) excluded ----------------------------------------------------------------------------
    "evaluations.evaluator": _excluded(),
    "evaluations.evaluationmessage": _excluded(),
    "evaluations.evaluationdataset": _excluded(),
    "evaluations.datasetautopopulationrule": _excluded(),
    "evaluations.evaluationconfig": _excluded(),
    "evaluations.evaluatortagrule": _excluded(),
    "evaluations.evaluationrun": _excluded(),
    "evaluations.evaluationresult": _excluded(),
    "evaluations.evaluationrunaggregate": _excluded(),
    "evaluations.appliedtag": _excluded(),
    "human_annotations.annotationqueue": _excluded(),
    "human_annotations.annotationitem": _excluded(),
    "human_annotations.annotation": _excluded(),
    "human_annotations.annotationqueueaggregate": _excluded(),
    "analysis.transcriptanalysis": _excluded(),
    "analysis.analysisquery": _excluded(),
}


def scope_class_for(model_label: str) -> ScopeClass:
    return CHATBOT_SCOPE_REGISTRY[model_label].scope_class
```

with `from collections.abc import Callable, Sequence`, `from dataclasses import dataclass` and `from enum import StrEnum` added to the module imports.

- [ ] **Step 4: Add `scoped_queryset` to the manifest**

In `apps/teams/export/manifest.py`, below `team_scoped_queryset`:

```python
def scoped_queryset(entry: ManifestEntry, team, scope: "ChatbotScope | None" = None) -> QuerySet:
    """The rows this request may serve: the team's, narrowed to the chatbot scope when one is active.

    ``_paginate`` takes a queryset, so pagination is untouched. An excluded resource returns nothing
    rather than the team's rows: importing one would reference an experiment that was never synced.
    """
    queryset = team_scoped_queryset(entry, team)
    if scope is None:
        return queryset
    rule = CHATBOT_SCOPE_REGISTRY[entry.model]
    if rule.build_q is None:
        return queryset.none()
    return queryset.filter(rule.build_q(scope))
```

with `from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY, ChatbotScope` at the top. The import runs one way only — `chatbot_scope` imports nothing from `manifest`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest apps/teams/export/tests/ -v`
Expected: PASS. If the partition test names a model, classify it — do not widen the test.

- [ ] **Step 6: Check every rule actually executes**

Add a smoke test that exercises each rule against an empty database, so a typo in a lookup path fails at CI time rather than mid-migration:

```python
@pytest.mark.django_db()
def test_every_scope_rule_builds_a_runnable_queryset():
    """A misspelt lookup path raises FieldError only when the queryset runs. Running each rule once
    against an empty database is enough to catch that."""
    from apps.teams.export import manifest
    from apps.teams.export.chatbot_scope import build_scope

    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team))
    scope = build_scope(team)

    for entry in manifest.MANIFEST_ENTRIES:
        list(manifest.scoped_queryset(entry, team, scope)[:1])
```

Run: `uv run pytest apps/teams/export/tests/test_chatbot_scope.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
uv run inv ruff --paths apps/teams/export
uv run inv typecheck --python --paths apps/teams/export
git add apps/teams/export
git commit -m "feat: classify and scope every synced model under a chatbot selection"
```

---

### Task 13: Serve the scope from the export API

`ResourceView.get` reads the scope off `request.team` and passes it to the queryset builder. No query parameter is added, so `resource_view` (`apps/api/export/views.py:208`) and `urls.py` are untouched and the OpenAPI surface moves only where the manifest and team schemas change.

The manifest gains a per-entry `scope`, derived from the registry, so the client knows which resources the source will serve empty.

The team endpoint reports the selection so the operator sees the server's answer rather than assuming, and so the client can key its cursors by it. Public ids, not pks: pks differ between servers.

**Files:**
- Modify: `apps/api/export/views.py:97-118`
- Modify: `apps/teams/export/manifest.py:19-24,285-297`
- Modify: `apps/api/export/serializers.py:137-153,209-230`
- Test: `apps/api/export/tests/test_views.py`

**Interfaces:**
- Consumes: `chatbot_scope.build_scope(team)`, `chatbot_scope.scope_class_for(label)`, `manifest.scoped_queryset(entry, team, scope)`.
- Produces:
  - `ManifestEntry.scope: str` — set from the registry in `build_manifest()`, not stored on the frozen entries.
  - Manifest payload entries gain `"scope": "owned" | "referenced" | "excluded"`.
  - `GET /api/export/team/` gains `exportable_chatbots: list[{public_id: str, name: str}]`, sorted by `public_id`, empty when the whole team is exportable.

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/export/tests/test_views.py`:

```python
def test_manifest_classifies_every_resource():
    response = APIClient().get(reverse("api:export:manifest"))
    entries = {e["resource"]: e["scope"] for e in response.json()["entries"]}
    assert entries["chatbots"] == "owned"
    assert entries["llm_providers"] == "referenced"
    assert entries["evaluators"] == "excluded"


def test_team_endpoint_reports_an_empty_selection(team):
    client = ApiTestClient(_admin(team), team)
    assert client.get(reverse("api:export:team")).json()["exportable_chatbots"] == []


def test_team_endpoint_reports_the_selection(team):
    chatbot = ExperimentFactory(team=team, name="Support bot")
    team.exportable_experiments.add(chatbot)

    client = ApiTestClient(_admin(team), team)
    body = client.get(reverse("api:export:team")).json()

    assert body["exportable_chatbots"] == [{"public_id": str(chatbot.public_id), "name": "Support bot"}]


def test_team_endpoint_never_exports_the_raw_allowlist(team):
    """The allowlist holds source pks the target cannot translate, and load_team imports the team
    before any experiment exists."""
    team.exportable_experiments.add(ExperimentFactory(team=team))
    client = ApiTestClient(_admin(team), team)
    assert "exportable_experiments" not in client.get(reverse("api:export:team")).json()


def test_a_resource_is_scoped_to_the_selected_chatbot(team):
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)

    client = ApiTestClient(_admin(team), team)
    ids = {row["id"] for row in client.get(_resource_url("chatbots")).json()["results"]}

    assert ids == {mine.id}
    assert theirs.id not in ids


def test_a_resource_is_unscoped_without_a_selection(team):
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)

    client = ApiTestClient(_admin(team), team)
    ids = {row["id"] for row in client.get(_resource_url("chatbots")).json()["results"]}

    assert {mine.id, theirs.id} <= ids


def test_an_excluded_resource_is_empty_while_a_selection_is_active(team):
    from apps.utils.factories.evaluations import EvaluatorFactory

    team.exportable_experiments.add(ExperimentFactory(team=team))
    EvaluatorFactory(team=team)

    client = ApiTestClient(_admin(team), team)
    body = client.get(_resource_url("evaluators")).json()

    assert body["results"] == []
    assert body["has_more"] is False
```

Add `from apps.utils.factories.experiment import ExperimentFactory` to the file's imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/api/export/tests/test_views.py -v`
Expected: FAIL — `KeyError: 'scope'`, `KeyError: 'exportable_chatbots'`, and both chatbots come back from the scoped resource.

- [ ] **Step 3: Put the scope class in the manifest payload**

In `apps/teams/export/manifest.py`:

```python
def build_manifest() -> dict:
    return {
        "schema_checksum": schema_checksum(),
        "entries": [
            {
                "model": e.model,
                "resource": e.resource,
                "cursor": e.cursor,
                "secret": e.secret,
                # Which rows a chatbot-scoped export serves for this model. The client skips an
                # "excluded" resource outright while a selection is active.
                "scope": scope_class_for(e.model).value,
            }
            for e in MANIFEST_ENTRIES
        ],
    }
```

with `scope_class_for` added to the `chatbot_scope` import.

In `apps/api/export/serializers.py`, extend `ManifestEntrySerializer`:

```python
    scope = serializers.CharField(
        help_text="How a chatbot-scoped export filters this model: owned | referenced | excluded. "
        "`excluded` resources are served empty while the team has a chatbot selection."
    )
```

- [ ] **Step 4: Report the selection on the team endpoint**

In `apps/api/export/serializers.py`, above `build_team_serializer`:

```python
class ExportableChatbotSerializer(serializers.Serializer):
    """One entry of a team's export allowlist. Identified by ``public_id``: row pks differ between
    servers, so the client keys its state on the id both servers agree on."""

    public_id = serializers.CharField()
    name = serializers.CharField()
```

and replace `build_team_serializer`:

```python
@cache
def build_team_serializer():
    """Serializer for the single-team endpoint (``GET /api/export/team/``). The team anchors the export
    surface and is served as one object rather than a page. It extends the importable team-row dump with
    the operational status the sync client preflights on: ``is_migrating`` (kept out of the importable
    row, so migration mode isn't replicated to the target), ``has_public_key`` -- a method field
    collapsing the key to a boolean, whether one is registered, never the key material itself -- and
    ``exportable_chatbots``, which says how much of the team this server will actually serve. The raw
    ``public_key`` and ``exportable_experiments`` fields are excluded alongside ``members``, so none of
    them ever goes out: the allowlist holds source pks the target cannot translate, and ``load_team``
    imports the team row before any experiment exists."""
    base = build_resource_serializer(entry_model(TEAM_MODEL))

    class TeamExportSerializer(base):
        has_public_key = serializers.SerializerMethodField(
            help_text="Whether the team has a public key registered. The key itself is never exported."
        )
        exportable_chatbots = serializers.SerializerMethodField(
            help_text="Chatbots this team may export. Empty means the whole team is exportable."
        )

        class Meta(base.Meta):
            exclude = ["members", "public_key", "exportable_experiments"]

        def get_has_public_key(self, team) -> bool:
            return bool(team.public_key)

        @extend_schema_field(ExportableChatbotSerializer(many=True))
        def get_exportable_chatbots(self, team) -> list[dict]:
            # Sorted by public_id so the client's selection key is stable across requests.
            rows = team.exportable_experiments.values_list("public_id", "name")
            return sorted(
                ({"public_id": str(public_id), "name": name} for public_id, name in rows),
                key=lambda row: row["public_id"],
            )

    return TeamExportSerializer
```

`Meta.exclude` on the subclass replaces the base's list rather than extending it, so
`exportable_experiments` has to be named here as well as in `EXCLUDE_REGISTRY`.

- [ ] **Step 5: Apply the scope in the view**

In `apps/api/export/views.py`:

```python
from apps.teams.export.chatbot_scope import build_scope
from apps.teams.export.manifest import (
    ManifestEntry,
    build_manifest,
    entry_model,
    get_manifest_entry,
    scoped_queryset,
)
```

```python
class ResourceView(_ExportAPIView):
    # The per-resource OpenAPI schema is attached by ``resource_view`` (below), not here.
    def get(self, request, resource):
        entry = get_manifest_entry(resource)
        if entry is None:
            raise NotFound("Unknown resource.")

        context = {"public_key": None, "team": request.team}
        if entry.secret:
            if not request.team.public_key:
                return Response(
                    {"detail": MISSING_PUBLIC_KEY_DETAIL},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            context["public_key"] = load_public_key(request.team.public_key)

        limit = _parse_limit(request.query_params.get("limit", DEFAULT_LIMIT))
        # The chatbot scope is the team's own allowlist, never a client preference: an API key alone
        # must not widen what may leave this server.
        queryset = scoped_queryset(entry, request.team, build_scope(request.team))
        rows, next_cursor, has_more = _paginate(queryset, entry.cursor, request.query_params.get("cursor"), limit)

        serializer = build_resource_serializer(entry_model(entry.model))(rows, many=True, context=context)
        return Response({"cursor": next_cursor, "has_more": has_more, "results": serializer.data})
```

`team_scoped_queryset` stays importable for the tests that pin team isolation directly.

- [ ] **Step 6: Run the API tests**

Run: `uv run pytest apps/api/export/tests/ apps/teams/export/tests/ -v`
Expected: PASS.

- [ ] **Step 7: Regenerate the committed schema and check it**

```bash
uv run python manage.py spectacular --api-version export --file api-schemas/export.yml
git diff --stat api-schemas/export.yml
```

Expected: the only changes are the new `scope` property on the manifest entry component, the new `exportable_chatbots` property on the team component, and the removal of `exportable_experiments` from it. No per-resource query parameter. CI regenerates this file, so committing it here only keeps the diff honest.

- [ ] **Step 8: Commit**

```bash
uv run inv ruff --paths apps/api/export apps/teams/export
uv run inv typecheck --python --paths apps/api/export
git add apps/api/export apps/teams/export/manifest.py api-schemas/export.yml
git commit -m "feat: serve the chatbot scope from the export API"
```

---

### Task 14: Teach the sync client about the selection

The client sends nothing — it reads the selection from the team endpoint. Three things follow:

1. **Skip excluded resources.** An excluded resource is served empty, so requesting it costs a round trip and nothing else. Skipping it is also what the report needs to state plainly.
2. **Key the cursors by the selection.** Cursors are already keyed by selection (Task 7); this fills in the real key.
3. **Seed a new selection's cursors from a superset.** Shrinking a selection leaves every cursor valid, since the source then serves a subset. Only a widening needs a fresh start.

**Files:**
- Modify: `apps/teams/export/translation.py`
- Modify: `apps/teams/management/commands/sync_team.py`
- Test: `apps/teams/export/tests/test_translation.py`, `apps/teams/export/tests/test_command.py`

**Interfaces:**
- Consumes: `client.get_team()["exportable_chatbots"]` (list of `{public_id, name}`), `entry["scope"]` from the manifest.
- Produces:
  - `translation.selection_key(public_ids: Sequence[str]) -> str` — `ALL_CHATBOTS_KEY` for empty, else a sha256 hex digest of the sorted ids.
  - `FKTranslationStore.record_selection(selection_key: str, public_ids: Sequence[str]) -> None`
  - `FKTranslationStore.selections() -> dict[str, list[str]]`
  - `FKTranslationStore.seed_cursors_from(source_key: str, target_key: str) -> None`
  - `sync_team.resolve_selection(client, store) -> tuple[str, list[dict]]` — the selection key to use and the chatbot rows the source reported, having seeded cursors if a superset selection was already synced.

- [ ] **Step 1: Write the failing store tests**

Append to `apps/teams/export/tests/test_translation.py`:

```python
from apps.teams.export.translation import selection_key


def test_selection_key_of_nothing_is_the_all_key():
    assert selection_key([]) == ALL_CHATBOTS_KEY


def test_selection_key_ignores_order():
    assert selection_key(["b", "a"]) == selection_key(["a", "b"])


def test_selection_key_differs_per_selection():
    assert selection_key(["a"]) != selection_key(["a", "b"])


def test_selections_round_trip(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.record_selection("k1", ["a", "b"])
    assert store.selections() == {"k1": ["a", "b"]}


def test_seed_cursors_copies_one_selection_to_another(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor("wide", "chat.chat", "c1")
    store.set_cursor("wide", "chat.chatmessage", "c2")

    store.seed_cursors_from("wide", "narrow")

    assert store.cursors_for("narrow") == {"chat.chat": "c1", "chat.chatmessage": "c2"}
    assert store.cursors_for("wide") == {"chat.chat": "c1", "chat.chatmessage": "c2"}


def test_seed_cursors_does_not_overwrite_an_existing_cursor(make_store, tmp_path):
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor("wide", "chat.chat", "c1")
    store.set_cursor("narrow", "chat.chat", "already")

    store.seed_cursors_from("wide", "narrow")

    assert store.get_cursor("narrow", "chat.chat") == "already"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: FAIL with `ImportError: cannot import name 'selection_key'`.

- [ ] **Step 3: Add the selection bookkeeping to the store**

In `apps/teams/export/translation.py`, add `import hashlib` and, in `__init__`:

```python
self._conn.execute("CREATE TABLE IF NOT EXISTS selections (selection_key TEXT PRIMARY KEY, public_ids TEXT NOT NULL)")
```

and:

```python
def selection_key(public_ids: Sequence[str]) -> str:
    """The cursor namespace for one chatbot selection. Order-independent, so the key only moves when
    the set itself does."""
    if not public_ids:
        return ALL_CHATBOTS_KEY
    digest = hashlib.sha256("\n".join(sorted(public_ids)).encode()).hexdigest()
    return digest[:32]
```

```python
def record_selection(self, selection_key: str, public_ids: Sequence[str]) -> None:
    """Remember which chatbots a key stands for, so a later run can tell a narrower selection
    from a wider one."""
    self._conn.execute(
        "INSERT INTO selections (selection_key, public_ids) VALUES (?, ?) "
        "ON CONFLICT (selection_key) DO UPDATE SET public_ids = excluded.public_ids",
        (selection_key, json.dumps(sorted(public_ids))),
    )
    self._conn.commit()


def selections(self) -> dict[str, list[str]]:
    return {key: json.loads(ids) for key, ids in self._conn.execute("SELECT selection_key, public_ids FROM selections")}


def seed_cursors_from(self, source_key: str, target_key: str) -> None:
    """Copy one selection's cursors to another, leaving any the target already has alone."""
    self._conn.execute(
        "INSERT INTO cursors (selection_key, model_label, cursor) "
        "SELECT ?, model_label, cursor FROM cursors WHERE selection_key = ? "
        "ON CONFLICT (selection_key, model_label) DO NOTHING",
        (target_key, source_key),
    )
    self._conn.commit()
```

with `from collections.abc import Sequence` added to the module imports.

- [ ] **Step 4: Run the store tests**

Run: `uv run pytest apps/teams/export/tests/test_translation.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing command tests**

Append to `apps/teams/export/tests/test_command.py`:

```python
def test_excluded_resources_are_not_requested_under_a_selection(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"].append(
        {
            "model": "evaluations.evaluator",
            "resource": "evaluators",
            "cursor": "pk",
            "secret": False,
            "scope": "excluded",
        }
    )
    manifest["entries"][0]["scope"] = "referenced"
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    store = make_store(tmp_path / "team.sqlite")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert "evaluators" not in {resource for resource, _cursor in client.iter_calls}


def test_excluded_resources_are_requested_without_a_selection(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"].append(
        {
            "model": "evaluations.evaluator",
            "resource": "evaluators",
            "cursor": "pk",
            "secret": False,
            "scope": "excluded",
        }
    )
    manifest["entries"][0]["scope"] = "referenced"
    store = make_store(tmp_path / "team.sqlite")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert "evaluators" in {resource for resource, _cursor in client.iter_calls}


def test_a_narrowed_selection_keeps_its_cursors(make_store, tmp_path, keypair):
    """The source then serves a subset of what it served, so every cursor is still valid; a re-read
    of the whole selection would cost hours on a large team."""
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"][0]["scope"] = "referenced"
    store = make_store(tmp_path / "team.sqlite")

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}, {"public_id": "b", "name": "B"}]
    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", "5") in client.iter_calls


def test_a_widened_selection_starts_from_the_beginning(make_store, tmp_path, keypair):
    """Adding a chatbot puts rows below the cursor that were never synced, so resuming would skip
    them silently."""
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"][0]["scope"] = "referenced"
    store = make_store(tmp_path / "team.sqlite")

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}, {"public_id": "b", "name": "B"}]
    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", None) in client.iter_calls
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest apps/teams/export/tests/test_command.py -k "selection or excluded" -v`
Expected: FAIL — every resource is requested and the cursor namespace never moves.

- [ ] **Step 7: Resolve the selection in `run_sync`**

In `apps/teams/management/commands/sync_team.py`:

```python
from apps.teams.export.translation import (
    ALL_CHATBOTS_KEY,
    FKTranslationStore,
    page_cursor,
    selection_key,
)
```

```python
def resolve_selection(client, store) -> tuple[str, list[dict]]:
    """The cursor namespace for this run, and the chatbots the source says it will serve.

    The selection is the source's own allowlist, so it can change between runs. Cursors are kept per
    selection: a narrower one reuses the cursors of any wider selection already synced, because the
    source then serves a subset of the same rows; a wider one has rows below those cursors that were
    never fetched, so it starts from the beginning.
    """
    chatbots = client.get_team().get("exportable_chatbots") or []
    public_ids = [chatbot["public_id"] for chatbot in chatbots]
    key = selection_key(public_ids)
    if not store.cursors_for(key):
        selected = set(public_ids)
        for other_key, other_ids in store.selections().items():
            if other_key != key and selected <= set(other_ids):
                store.seed_cursors_from(other_key, key)
                break
    store.record_selection(key, public_ids)
    return key, chatbots
```

`ALL_CHATBOTS_KEY` is what `selection_key([])` returns, so a store written before this task keeps its cursors when the source has no selection. Seed from the `"all"` namespace as well: it is a superset of every selection, and `store.selections()` will not list it unless a run recorded it — record it unconditionally above, which the code does.

Then rewrite the loop:

```python
def run_sync(
    client,
    store,
    private_key,
    write=lambda _m: None,
    page_limit=100,
    enforce_schema=True,
    on_user_created=send_password_reset_email,
    style=None,
):
    manifest = check_sync_preconditions(client, private_key, enforce_schema)
    cursor_key, chatbots = resolve_selection(client, store)

    importer = Importer(
        store,
        private_key=private_key,
        on_user_created=on_user_created,
        fetch_file_content=client.get_file_content,
    )
    importer.synced_chatbots = chatbots
    try:
        with mute_signals():
            load_team(importer, client, store)
            for entry in manifest["entries"]:
                if chatbots and entry.get("scope") == "excluded":
                    write(_style_synced_line(f"skipped {entry['resource']} (not migrated)", 0, style))
                    continue
                count = _sync_resource(importer, client, store, entry, page_limit, cursor_key)
                write(_style_synced_line(f"synced {count} {entry['resource']} rows", count, style))
    except requests.HTTPError as exc:
        friendly = _friendly_http_error_message(exc)
        if friendly is None:
            raise
        raise CommandError(friendly) from exc
    return importer
```

and drop the now-unused `cursor_key=ALL_CHATBOTS_KEY` parameter added in Task 7 — `resolve_selection` supplies it.

Add `self.synced_chatbots: list[dict] = []` to `Importer.__init__` so the report has it whether or not `run_sync` set it.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest apps/teams/export/tests/ -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
uv run inv ruff --paths apps/teams/export apps/teams/management/commands/sync_team.py
uv run inv typecheck --python --paths apps/teams
git add apps/teams
git commit -m "feat: key the sync's cursors on the source's chatbot selection"
```

---

## Phase 4 — the freeze and the operator surface

---

### Task 15: Freeze only the selected chatbots

`migrating_team_ids()` (`apps/teams/export_service.py`) stops every trigger and scheduled message the team owns (`apps/events/tasks.py:37,54,81,105`) — far too blunt when one chatbot is moving.

Two performance constraints on the replacement, because `_get_static_triggers_to_fire` runs per conversation event and `enqueue_timed_out_events` runs every ten seconds install-wide (`config/settings.py:611`):

- No m2m join, `isnull=True` on a reverse relation, or three-branch OR inside the `exclude()`. Django compiles a multi-valued `exclude()` to `NOT (pk IN (subquery))`, which is both a planner trap and a semantics trap.
- Both sides stay lazy subqueries over plain indexed columns.

The family expansion is required: triggers are versioned alongside their chatbot, so a trigger on version 3 points at the version-3 row, not the working version the allowlist holds.

**Files:**
- Modify: `apps/teams/export_service.py`
- Modify: `apps/events/tasks.py:13,37,54,81,105`
- Test: `apps/teams/tests/test_migration_lock.py`, `apps/events/tests/`

**Interfaces:**
- Consumes: `Team.exportable_experiments.through`.
- Produces: `frozen_experiment_q(path: str = "experiment") -> Q`. `migrating_team_ids()` is deleted.

- [ ] **Step 1: Write the failing test**

Create `apps/teams/tests/test_export_service.py`:

```python
import pytest

from apps.events.models import StaticTrigger
from apps.teams.export_service import frozen_experiment_q
from apps.utils.factories.events import StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def _firing(queryset=None):
    return set((queryset or StaticTrigger.objects).exclude(frozen_experiment_q()).values_list("id", flat=True))


def test_nothing_is_frozen_when_no_team_is_migrating():
    trigger = StaticTriggerFactory(experiment=ExperimentFactory(team=TeamFactory()))
    assert trigger.id in _firing()


def test_a_migrating_team_without_a_selection_freezes_everything():
    team = TeamFactory(is_migrating=True)
    trigger = StaticTriggerFactory(experiment=ExperimentFactory(team=team))
    assert trigger.id not in _firing()


def test_a_selection_freezes_only_the_selected_chatbots():
    team = TeamFactory(is_migrating=True)
    migrating = ExperimentFactory(team=team)
    still_running = ExperimentFactory(team=team)
    team.exportable_experiments.add(migrating)

    frozen = StaticTriggerFactory(experiment=migrating)
    live = StaticTriggerFactory(experiment=still_running)

    firing = _firing()
    assert frozen.id not in firing
    assert live.id in firing


def test_a_trigger_on_a_published_version_is_frozen_with_its_family():
    """Triggers are versioned alongside their chatbot, so a trigger on version 3 points at the
    version-3 row, not the working version the allowlist holds."""
    team = TeamFactory(is_migrating=True)
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)
    team.exportable_experiments.add(working)

    trigger = StaticTriggerFactory(experiment=published)
    assert trigger.id not in _firing()


def test_a_selection_on_a_team_not_migrating_freezes_nothing():
    team = TeamFactory(is_migrating=False)
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)

    trigger = StaticTriggerFactory(experiment=chatbot)
    assert trigger.id in _firing()


def test_the_path_argument_addresses_a_different_relation():
    from apps.events.models import ScheduledMessage

    team = TeamFactory(is_migrating=True)
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)

    # The queryset compiles: this is what poll_scheduled_messages does.
    assert ScheduledMessage.objects.exclude(frozen_experiment_q("experiment")).count() >= 0


def test_frozen_experiment_q_issues_no_extra_query(django_assert_num_queries):
    """Both sides are lazy subqueries, so building the Q costs nothing and running it is one
    statement -- it lands on paths that run thousands of times a day."""
    TeamFactory(is_migrating=True)
    with django_assert_num_queries(0):
        frozen_experiment_q()
    with django_assert_num_queries(1):
        list(StaticTrigger.objects.exclude(frozen_experiment_q()).values_list("id", flat=True))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest apps/teams/tests/test_export_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'frozen_experiment_q'`.

- [ ] **Step 3: Write the Q builder**

Replace `apps/teams/export_service.py`:

```python
"""Which chatbots migration mode stops from firing.

An empty allowlist still means "everything", so a migrating team with no selection freezes all of
its chatbots as before, while one with a selection freezes only the listed families.
"""

from django.db.models import Q

from apps.experiments.models import Experiment

from .models import Team


def frozen_experiment_q(path: str = "experiment") -> Q:
    """Experiments whose outbound firing is frozen by migration mode.

    ``path`` is the lookup from the model being filtered to its experiment. Callers apply it with
    ``.exclude(frozen_experiment_q())``; both branches are single-valued column predicates over lazy
    subqueries, so the exclude compiles to a plain ``NOT (a OR b)`` with no multi-valued join inside
    it -- these run per conversation event and every ten seconds install-wide.
    """
    allowlist = Team.exportable_experiments.through.objects.filter(team__is_migrating=True)
    selected_ids = allowlist.values("experiment_id")
    team_wide_ids = Team.objects.filter(is_migrating=True).exclude(id__in=allowlist.values("team_id")).values("id")
    family_ids = Experiment._base_manager.filter(
        Q(pk__in=selected_ids) | Q(working_version_id__in=selected_ids)
    ).values("id")
    return Q(**{f"{path}__team_id__in": team_wide_ids}) | Q(**{f"{path}_id__in": family_ids})
```

The expansion is written out here rather than reusing `selection.expand_to_family`, which takes a
materialised id list; this predicate has to stay a lazy subquery. If importing `Experiment` at module
level raises a circular import (this module is imported by `apps/events/tasks.py`), use
`apps.get_model("experiments", "Experiment")` inside the function and say why in a comment.

- [ ] **Step 4: Switch the four call sites**

In `apps/events/tasks.py`, replace the import and each `.exclude(...)`:

```python
from apps.teams.export_service import frozen_experiment_q
```

- `_get_static_triggers_to_fire` (`:37`): `.exclude(frozen_experiment_q())`
- `enqueue_timed_out_events` (`:54`): `.exclude(frozen_experiment_q())`
- `poll_due_scheduled_triggers` (`:81`): `.exclude(frozen_experiment_q())`
- `poll_scheduled_messages` (`:105`): `.exclude(frozen_experiment_q())` — it currently excludes on `team_id`, but `ScheduledMessage` has its own non-null `experiment` FK, so it takes the same shape as the other three.

These are forward FK chains, so nothing multiplies rows and ADR-0037 does not apply.

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest apps/teams/tests/test_export_service.py apps/teams/tests/test_migration_lock.py apps/events/tests/ -v`
Expected: PASS. Any test still importing `migrating_team_ids` fails here — update it to `frozen_experiment_q`.

- [ ] **Step 6: Update the migration-mode help text**

Now that the freeze is per-chatbot, the card's wording can follow the radio. In `templates/teams/manage_team.html`, replace the migration-mode paragraph with:

```html
                      <p class="text-neutral-500 section-subtitle whitespace-normal"
                         x-show="scope === 'all'">
                        {% translate "While enabled, scheduled messages and event triggers (including timeout triggers) for this team are paused and will not fire." %}
                      </p>
                      <p class="text-neutral-500 section-subtitle whitespace-normal"
                         x-show="scope === 'selected'" x-cloak>
                        {% translate "While enabled, scheduled messages and event triggers for the selected chatbots are paused and will not fire. Other chatbots keep running." %}
                      </p>
```

- [ ] **Step 7: Commit**

```bash
uv run inv ruff --paths apps/teams apps/events
uv run inv typecheck --python --paths apps/teams apps/events
git add apps/teams apps/events templates/teams/manage_team.html
git commit -m "feat: freeze only the chatbots a team is actually migrating"
```

---

### Task 16: Tell the operator what was and was not synced

`_report` (`apps/teams/management/commands/sync_team.py:364`) lists what needs manual setup. Under a selection it must also name the chatbots that moved and state plainly that evaluations, human annotations and transcript analyses did not.

`force_delete_team` (`:274`) deletes the whole local team, which would destroy chatbots synced under an earlier selection. Refuse when the source reports a non-empty allowlist and say what to do instead.

`check_source_team_ready` (`:104`) prints the source's answer so the operator sees it rather than assuming.

**Files:**
- Modify: `apps/teams/management/commands/sync_team.py:104-117,274-287,322-352,364-423`
- Test: `apps/teams/export/tests/test_command.py`

**Interfaces:**
- Consumes: `Importer.synced_chatbots: list[dict]`, `Importer.skipped_rows: int`, `client.get_team()["exportable_chatbots"]`.
- Produces: `Command._report(..., chatbots: Sequence[dict] = (), skipped_rows: int = 0)`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/teams/export/tests/test_command.py`:

```python
def test_report_names_the_synced_chatbots(capsys):
    command = Command()
    command._report(
        sync_complete=True,
        team_slug="acme",
        chatbots=[{"public_id": "abc", "name": "Support bot"}],
    )
    out = capsys.readouterr().out
    assert "Support bot" in out
    assert "evaluations" in out.lower()
    assert "human annotations" in out.lower()
    assert "transcript analyses" in out.lower()


def test_report_omits_the_chatbot_section_for_a_whole_team_sync(capsys):
    command = Command()
    command._report(sync_complete=True, team_slug="acme", chatbots=[])
    out = capsys.readouterr().out
    assert "Chatbots synced" not in out


def test_force_delete_is_refused_while_the_source_has_a_selection(make_store, tmp_path, keypair):
    """--force-delete drops the whole local team, which would destroy chatbots synced under an
    earlier selection."""
    public_key, _private = keypair
    _manifest_payload, rows = _scenario(public_key)
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    client = FakeClient(_manifest_payload, rows)

    with pytest.raises(CommandError, match="only part of the team"):
        sync_team.check_force_delete_allowed(client)


def test_force_delete_is_allowed_for_a_whole_team_sync(keypair):
    public_key, _private = keypair
    _manifest_payload, rows = _scenario(public_key)
    client = FakeClient(_manifest_payload, rows)

    sync_team.check_force_delete_allowed(client)  # does not raise
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/teams/export/tests/test_command.py -k "report or force_delete_is" -v`
Expected: FAIL — `_report` takes no `chatbots`, and `check_force_delete_allowed` does not exist.

- [ ] **Step 3: Add the force-delete guard**

In `apps/teams/management/commands/sync_team.py`:

```python
FORCE_DELETE_WITH_SELECTION = (
    "The source is exporting only part of the team, so --force-delete would also destroy chatbots "
    "synced under an earlier selection. Delete the chatbots you want re-imported by hand on this "
    "server, then rerun without --force-delete."
)


def check_force_delete_allowed(client) -> None:
    """Refuse a whole-team delete while the source is exporting a selection."""
    if client.get_team().get("exportable_chatbots"):
        raise CommandError(FORCE_DELETE_WITH_SELECTION)
```

and call it first in `_run_force_delete`:

```python
    def _run_force_delete(self, options, client):
        """Confirm and delete the local team plus its sync state."""
        check_force_delete_allowed(client)
        if not self._confirm_force_delete(options["team_slug"]):
            raise CommandError("Aborted: --force-delete not confirmed.")
        force_delete_team(
            options["team_slug"],
            options["state_dir"],
            write=lambda message: self.stdout.write(self.style.WARNING(message)),
        )
```

with `handle` passing the client: `self._run_force_delete(options, client)`.

- [ ] **Step 4: Report the selection during preflight**

In `check_source_team_ready`:

```python
def check_source_team_ready(client, write=lambda _m: None) -> None:
    """Block the sync unless the source team is in migration mode and has a public key registered --
    both must be set. The export API no longer enforces migration mode server-side, so the client
    checks it here from the team endpoint's ``is_migrating`` / ``has_public_key`` status (the latter is a
    boolean saying whether a key is registered). Also prints what the source says it will export, so
    the operator sees the server's answer rather than assuming. Raises CommandError listing whatever
    is missing."""
    team = client.get_team()
    problems = []
    if not team.get("is_migrating"):
        problems.append(MIGRATION_MODE_REQUIRED)
    if not team.get("has_public_key"):
        problems.append(MISSING_PUBLIC_KEY_MESSAGE)
    if problems:
        raise CommandError(" ".join(problems))

    chatbots = team.get("exportable_chatbots") or []
    if chatbots:
        write(f"The source will export {len(chatbots)} of this team's chatbots:")
        for chatbot in chatbots:
            write(f"  - {chatbot['name']}")
    else:
        write("The source will export the whole team.")
```

Thread `write` through `check_sync_preconditions(client, private_key, enforce_schema=True, store=None, write=lambda _m: None)` and pass `self.stdout.write` from `handle`.

- [ ] **Step 5: Extend the report**

```python
    def _report(
        self,
        *,
        sync_complete: bool,
        team_slug: str,
        duration: timedelta | None = None,
        missing_files: Sequence[str] = (),
        notification_failures: Sequence[tuple[str, str]] = (),
        chatbots: Sequence[dict] = (),
        skipped_rows: int = 0,
    ) -> None:
        """Print everything the operator needs after a sync, so ``handle`` stays a thin wiring shell:
        what was moved, which resources need manual setup, which files the source had no content for,
        whether the sync finished or must be rerun, how long the run took, and the follow-up step for
        channel webhooks (a separate command -- see ``reregister_webhooks``). Sections are headed and
        blank-line separated so the report stands apart from the row-by-row progress log above it."""
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Sync report"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if duration is not None:
            self.stdout.write(f"Duration: {duration}")
        if skipped_rows:
            self.stdout.write(f"Rows already up to date and left untouched: {skipped_rows}")

        if chatbots:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Chatbots synced"))
            for chatbot in chatbots:
                self.stdout.write(f"  - {chatbot['name']}")
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Only these chatbots were migrated. Evaluations, human annotations and transcript "
                    "analyses were not migrated for them, and neither was anything belonging to this "
                    "team's other chatbots."
                )
            )

        # ... the existing missing_files / notification_failures / webhooks / manual-resources
        # sections follow unchanged ...
```

and pass the values in `handle`:

```python
            self._report(
                sync_complete=not store.has_unfilled_targets(),
                team_slug=options["team_slug"],
                duration=duration,
                missing_files=importer.missing_files,
                notification_failures=importer.notification_failures,
                chatbots=importer.synced_chatbots,
                skipped_rows=importer.skipped_rows,
            )
```

Also update the command's module docstring (`:1-17`) to say the source decides how much of the team moves, and that the command has no chatbot flag: it syncs whatever the source allows.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_command.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
uv run inv ruff --paths apps/teams/management/commands/sync_team.py apps/teams/export/tests/test_command.py
uv run inv typecheck --python --paths apps/teams
git add apps/teams
git commit -m "feat: report which chatbots a sync moved and refuse a partial force-delete"
```

---

### Task 17: Back the files prompt off for a partial sync

`check_sync_preconditions` (`apps/teams/management/commands/sync_team.py:128`) makes the operator
confirm they have already moved the team's files into this server's storage, and refuses to run
otherwise. That prompt assumes a whole-team move: `create_team_files_zip_task` only zips whole teams,
so for a partial sync there is no bundle to have moved.

The importer already has the per-file answer — `_handle_missing_object` (`importer.py:308`) fetches a
missing blob from the source and retries, recording the ones the source cannot supply. Under a
selection, say that rather than demanding a bundle nobody can produce.

**Files:**
- Modify: `apps/teams/management/commands/sync_team.py:128-167`
- Test: `apps/teams/export/tests/test_command.py`

**Interfaces:**
- Consumes: `client.get_team()["exportable_chatbots"]`, `FKTranslationStore.has_flag` / `set_flag`, `FILES_CONFIRMED_FLAG`.
- Produces: no new names. `check_sync_preconditions` keeps its signature from Task 16
  (`client, private_key, enforce_schema=True, store=None, write=lambda _m: None`).

- [ ] **Step 1: Write the failing tests**

Append to `apps/teams/export/tests/test_command.py`:

```python
def test_a_partial_sync_does_not_ask_about_the_files_bundle(make_store, tmp_path, keypair, monkeypatch):
    """create_team_files_zip_task only zips whole teams, so there is no bundle for the operator to
    have moved. The importer backfills each missing blob from the source instead."""
    public_key, private = keypair
    manifest_payload, rows = _scenario(public_key)
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    store = make_store(tmp_path / "team.sqlite")
    monkeypatch.setattr(sync_team, "_prompt", lambda _message: pytest.fail("prompted for the files bundle"))

    lines = []
    check_sync_preconditions(FakeClient(manifest_payload, rows), private, store=store, write=lines.append)

    assert any("backfilled from the source" in line for line in lines)


def test_a_whole_team_sync_still_asks_about_the_files_bundle(make_store, tmp_path, keypair, monkeypatch):
    public_key, private = keypair
    manifest_payload, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")
    asked = []
    monkeypatch.setattr(sync_team, "_prompt", lambda message: asked.append(message) or "yes")

    check_sync_preconditions(FakeClient(manifest_payload, rows), private, store=store)

    assert asked
    assert store.has_flag(sync_team.FILES_CONFIRMED_FLAG)


def test_a_whole_team_sync_aborts_when_the_files_were_not_moved(make_store, tmp_path, keypair, monkeypatch):
    public_key, private = keypair
    manifest_payload, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")
    monkeypatch.setattr(sync_team, "_prompt", lambda _message: "no")

    with pytest.raises(CommandError, match="storage backend"):
        check_sync_preconditions(FakeClient(manifest_payload, rows), private, store=store)

    assert not store.has_flag(sync_team.FILES_CONFIRMED_FLAG)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest apps/teams/export/tests/test_command.py -k files -v`
Expected: FAIL — the first test fails because the prompt fires regardless of the selection.

- [ ] **Step 3: Branch the precondition**

Replace the files block in `check_sync_preconditions`:

```python
    check_source_team_ready(client, write=write)

    partial = bool(client.get_team().get("exportable_chatbots"))
    if partial:
        # The files export bundles a whole team, so there is nothing for the operator to have moved.
        # Importer._handle_missing_object fetches each blob from the source as its row lands, and the
        # report lists the ones the source had no content for.
        write("Files for the selected chatbots will be backfilled from the source as they are needed.")
        return manifest

    files_confirmation_needed = store is not None and not store.has_flag(FILES_CONFIRMED_FLAG)
    if files_confirmation_needed:
        answer = _prompt(
            "Have you exported the team's files from the source server and imported them into "
            "this server's storage backend? [yes/no]: "
        )
        if answer.strip().lower() != "yes":
            raise CommandError(
                "The team's files must be exported from the source server and imported into this "
                "server's storage backend before syncing, otherwise the sync will fail. Do that "
                "first, then rerun this command."
            )
        store.set_flag(FILES_CONFIRMED_FLAG)
    return manifest
```

The original set the flag in a second `if` on the same condition; folding it into the first block
keeps the behaviour (set only after every check passed) and removes the duplicate test.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest apps/teams/export/tests/test_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run inv ruff --paths apps/teams/management/commands/sync_team.py apps/teams/export/tests/test_command.py
git add apps/teams
git commit -m "feat: backfill files per row instead of demanding a bundle for a partial sync"
```

---

### Task 18: Limit webhook re-registration to the chatbots that moved

`reregister_webhooks` takes `--team-slug` only (`apps/teams/management/commands/reregister_webhooks.py:91`). After a partial sync it would repoint the webhooks of chatbots still live on the source, silently cutting them over.

**Files:**
- Modify: `apps/teams/management/commands/reregister_webhooks.py:55-84,87-112`
- Test: `apps/teams/tests/test_reregister_webhooks_command.py`

**Interfaces:**
- Consumes: `apps.teams.export.selection.expand_to_family(ids)`.
- Produces: `reregister_webhooks(team, chatbot_public_ids: Sequence[str] = ()) -> WebhookReregistrationReport`, and a repeatable `--chatbot` flag taking a chatbot's `public_id`.

- [ ] **Step 1: Write the failing test**

Append to `apps/teams/tests/test_reregister_webhooks_command.py`:

```python
def test_only_the_named_chatbots_channels_are_touched():
    """After a partial sync the other chatbots are still live on the source; repointing their
    webhooks would cut them over without anyone asking."""
    team = TeamFactory()
    moved = ExperimentFactory(team=team)
    stayed = ExperimentFactory(team=team)
    moved_channel = ExperimentChannelFactory(team=team, experiment=moved, platform=ChannelPlatform.TELEGRAM)
    ExperimentChannelFactory(team=team, experiment=stayed, platform=ChannelPlatform.TELEGRAM)

    report = reregister_webhooks(team, [str(moved.public_id)])

    labels = [label for label, *_ in report.manual] + report.updated
    assert any(moved.name in label for label in labels)
    assert not any(stayed.name in label for label in labels)


def test_every_channel_is_touched_without_the_flag():
    team = TeamFactory()
    one = ExperimentFactory(team=team)
    two = ExperimentFactory(team=team)
    ExperimentChannelFactory(team=team, experiment=one, platform=ChannelPlatform.TELEGRAM)
    ExperimentChannelFactory(team=team, experiment=two, platform=ChannelPlatform.TELEGRAM)

    report = reregister_webhooks(team)

    labels = [label for label, *_ in report.manual] + report.updated
    assert any(one.name in label for label in labels)
    assert any(two.name in label for label in labels)


def test_an_unknown_chatbot_id_is_an_error():
    team = TeamFactory()
    with pytest.raises(CommandError, match="not found"):
        call_command(
            "reregister_webhooks",
            f"--team-slug={team.slug}",
            "--chatbot=00000000-0000-0000-0000-000000000000",
            "--noinput",
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest apps/teams/tests/test_reregister_webhooks_command.py -v`
Expected: FAIL — `reregister_webhooks()` takes one argument.

- [ ] **Step 3: Add the filter**

In `apps/teams/management/commands/reregister_webhooks.py`:

```python
def reregister_webhooks(team, chatbot_public_ids: Sequence[str] = ()) -> WebhookReregistrationReport:
    """Repoint the team's channel webhooks at this server (re-registering a correct one is harmless).

    ``chatbot_public_ids`` narrows the run to those chatbots and their versions, for a migration that
    moved only part of the team: repointing a chatbot that is still live on the source would cut it
    over without anyone asking. Empty means every channel the team has.

    Channels that can't be updated automatically -- unsupported provider, undeterminable webhook URL,
    or a failed provider call -- are collected for manual setup rather than raising, so one bad
    channel can't fail the whole run.
    """
    report = WebhookReregistrationReport(updated=[], manual=[])
    channels = ExperimentChannel.objects.filter(team=team).select_related("experiment", "messaging_provider")
    if chatbot_public_ids:
        selected = Experiment._base_manager.filter(team=team, public_id__in=chatbot_public_ids)
        channels = channels.filter(experiment__in=expand_to_family(list(selected.values_list("id", flat=True))))
    for channel in channels:
        ...  # unchanged
```

with `from collections.abc import Sequence`, `from apps.experiments.models import Experiment` and `from apps.teams.export.selection import expand_to_family` added to the imports.

Then the flag and its validation:

```python
def add_arguments(self, parser):
    parser.add_argument("--team-slug", required=True, help="Slug of the local team to update.")
    parser.add_argument(
        "--chatbot",
        action="append",
        default=[],
        dest="chatbot_public_ids",
        metavar="PUBLIC_ID",
        help=(
            "Public id of a chatbot to update, repeatable. Use it after a sync that moved only "
            "some of the team's chatbots; without it every channel the team has is repointed."
        ),
    )
    parser.add_argument(
        "--noinput",
        "--no-input",
        action="store_false",
        dest="interactive",
        help="Skip the domain confirmation prompt (for non-interactive runs).",
    )


def handle(self, *args, **options):
    team = Team.objects.filter(slug=options["team_slug"]).first()
    if team is None:
        raise CommandError(f"No local team '{options['team_slug']}' found.")

    requested = options["chatbot_public_ids"]
    if requested:
        found = set(
            Experiment._base_manager.filter(team=team, public_id__in=requested).values_list("public_id", flat=True)
        )
        missing = [value for value in requested if value not in {str(pk) for pk in found}]
        if missing:
            raise CommandError(f"Chatbot(s) not found in team '{team.slug}': {', '.join(missing)}")

    if options.get("interactive", True) and not self._confirm_site_url():
        raise CommandError(
            "Aborted: fix this server's domain, then re-run. Update the Site record in the Django "
            "admin (Sites), or set SITE_URL_ROOT in the environment when running with DEBUG on."
        )

    report = reregister_webhooks(team, requested)
    self._report(report)
```

- [ ] **Step 4: Point the sync report at the new flag**

In `apps/teams/management/commands/sync_team.py`, `_report`'s webhook section:

```python
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Channel webhooks were not re-registered."))
        if chatbots:
            flags = " ".join(f"--chatbot={chatbot['public_id']}" for chatbot in chatbots)
            self.stdout.write(
                f"  Run `manage.py reregister_webhooks --team-slug={team_slug} {flags}` to point the "
                "synced chatbots' channel webhooks at this server. The other chatbots are still live "
                "on the source, so leave theirs alone."
            )
        else:
            self.stdout.write(
                f"  Run `manage.py reregister_webhooks --team-slug={team_slug}` to point this team's "
                "channel webhooks at this server."
            )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest apps/teams/tests/test_reregister_webhooks_command.py apps/teams/export/tests/test_command.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run inv ruff --paths apps/teams/management/commands apps/teams/tests/test_reregister_webhooks_command.py
uv run inv typecheck --python --paths apps/teams
git add apps/teams
git commit -m "feat: limit webhook re-registration to the chatbots a sync moved"
```

---

### Task 19: Drive a partial sync end to end

Tests and typecheck are necessary but not sufficient. A scoped sync has a runtime surface that only shows up when two servers talk to each other, so exercise it and observe the behaviour.

**Files:**
- Create: `apps/teams/export/tests/test_partial_sync_integration.py`
- Modify: none

**Interfaces:**
- Consumes: everything built above.
- Produces: no new names.

- [ ] **Step 1: Write the fixture**

Create `apps/teams/export/tests/test_partial_sync_integration.py`:

```python
"""One selection, served by the real serializers and imported by the real importer.

The unit tests pin each piece; this pins that the pieces agree -- in particular that nothing the
scope serves references a row the scope leaves out, which the importer otherwise surfaces as
UnresolvedForeignKey deep in a run rather than at CI time.
"""

import pytest

from apps.api.export.serializers import build_resource_serializer, build_team_serializer
from apps.evaluations.models import Evaluator
from apps.experiments.models import Experiment
from apps.service_providers.models import LlmProvider
from apps.teams.export.chatbot_scope import build_scope
from apps.teams.export.importer import Importer, mute_signals
from apps.teams.export.manifest import MANIFEST_ENTRIES, TEAM_MODEL, entry_model, scoped_queryset
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.chat import ChatMessageFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.events import ScheduledTriggerFactory, StaticTriggerFactory
from apps.utils.factories.experiment import (
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


class Fixture:
    """A team with two chatbots: one selected for migration, one that must stay behind."""

    def __init__(self):
        self.team = TeamFactory()
        self.used_provider = LlmProviderFactory(team=self.team, name="Used")
        self.unused_provider = LlmProviderFactory(team=self.team, name="Unused")
        self.collection = CollectionFactory(team=self.team, llm_provider=None, embedding_provider_model=None)
        self.collection_file = FileFactory(team=self.team)
        CollectionFileFactory(collection=self.collection, file=self.collection_file)
        self.source_material = SourceMaterialFactory(team=self.team)
        self.consent_form = ConsentFormFactory(team=self.team)

        pipeline = PipelineFactory(team=self.team)
        NodeFactory(
            pipeline=pipeline,
            llm_provider=self.used_provider,
            collection=self.collection,
            source_material=self.source_material,
        )
        self.migrating = ExperimentFactory(
            team=self.team, name="Migrating bot", pipeline=pipeline, consent_form=self.consent_form
        )
        ExperimentChannelFactory(team=self.team, experiment=self.migrating)
        StaticTriggerFactory(experiment=self.migrating)
        ScheduledTriggerFactory(experiment=self.migrating)
        session = ExperimentSessionFactory(experiment=self.migrating, team=self.team)
        self.message = ChatMessageFactory(chat=session.chat)
        attachment = session.chat.attachments.create(tool_type="code_interpreter")
        self.attached_file = FileFactory(team=self.team)
        attachment.files.add(self.attached_file)
        ParticipantDataFactory(team=self.team, experiment=self.migrating, participant=session.participant)

        staying_pipeline = PipelineFactory(team=self.team)
        NodeFactory(pipeline=staying_pipeline, llm_provider=self.unused_provider)
        self.staying = ExperimentFactory(team=self.team, name="Staying bot", pipeline=staying_pipeline)
        staying_session = ExperimentSessionFactory(experiment=self.staying, team=self.team)
        self.staying_message = ChatMessageFactory(chat=staying_session.chat)

        self.evaluator = EvaluatorFactory(team=self.team)
        self.team.exportable_experiments.add(self.migrating)


def _import_scoped_export(store) -> Importer:
    """Serialize every resource the scope serves and import it, exactly as a run would."""
    fixture = Fixture()
    importer = Importer(store)
    scope = build_scope(fixture.team)

    with mute_signals():
        team_row = build_team_serializer()(fixture.team).data
        importer.import_rows(TEAM_MODEL, [team_row])
        for entry in MANIFEST_ENTRIES:
            model = entry_model(entry.model)
            rows = scoped_queryset(entry, fixture.team, scope).order_by("id")
            serializer = build_resource_serializer(model)(
                list(rows), many=True, context={"team": fixture.team, "public_key": None}
            )
            importer.import_rows(entry.model, serializer.data)

    importer.fixture = fixture
    return importer
```

Secret resources are sealed with the team's public key; the fixture registers none, so `seal()` passes
the value through and no private key is needed. If `seal` requires a key, give `Fixture.team` one and
pass the matching private key to `Importer` — `apps/teams/export/tests/test_command.py`'s `keypair`
fixture already builds a pair.

- [ ] **Step 2: Write the assertions**

```python
def test_a_scoped_export_imports_without_an_unresolved_reference(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    assert target != importer.fixture.team
    names = set(Experiment._base_manager.filter(team=target).values_list("name", flat=True))
    assert "Migrating bot" in names


def test_the_other_chatbot_stays_behind(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    names = set(Experiment._base_manager.filter(team=target).values_list("name", flat=True))
    assert "Staying bot" not in names
    assert not ChatMessage.objects.filter(chat__team=target, content=importer.fixture.staying_message.content)


def test_the_excluded_classes_stay_behind(make_store, tmp_path):
    """An evaluator referencing an experiment that was never synced would make resolve_fk raise
    mid-import; serving these empty is what keeps the run clean."""
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    assert not Evaluator.objects.filter(team=importer.target_team).exists()


def test_only_the_referenced_shared_resources_came_across(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    assert LlmProvider.objects.filter(team=target, name="Used").exists()
    assert not LlmProvider.objects.filter(team=target, name="Unused").exists()


def test_the_chatbots_files_came_across(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    names = set(File.objects.filter(team=target).values_list("name", flat=True))
    assert importer.fixture.collection_file.name in names
    assert importer.fixture.attached_file.name in names
```

Add `from apps.chat.models import ChatMessage` and `from apps.files.models import File` to the imports.

Adjust factory keyword names and the `File` identity column to what the repo's factories actually
produce; run one assertion first and fix the call sites before writing the rest.

- [ ] **Step 3: Run it**

Run: `uv run pytest apps/teams/export/tests/test_partial_sync_integration.py -v`
Expected: PASS. An `UnresolvedForeignKey` here names the exact model and FK whose scope rule is too narrow — widen that rule in `CHATBOT_SCOPE_REGISTRY`, do not loosen the assertion.

- [ ] **Step 4: Drive it against a running server**

```bash
uv run inv dev
```

In one shell, set a public key, pick one chatbot and turn migration mode on at `/a/<slug>/team/#data`. In another, against a second local deployment:

```bash
uv run python manage.py sync_team --source-url=http://localhost:8000 --api-key=<key> --team-slug=<slug> --private-key-path=<path> --state-dir=/tmp/sync-state --limit=1000
```

Confirm from the output: the preflight names the selected chatbot; the excluded resources print as skipped; the report names the chatbot and says what was not migrated; the `reregister_webhooks` line carries `--chatbot=`. Then on the target, open the imported chatbot and send it a message — chat/pipeline changes emit `Trace`/`Span` records (`apps/trace/models.py`), so inspect those to confirm the pipeline took the path you expect rather than inferring from logs.

Then widen the selection on the source, rerun, and confirm the second chatbot arrives and the first one's rows are not duplicated.

- [ ] **Step 5: Run the whole suite once**

Run: `uv run pytest apps/teams apps/api/export apps/events -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run inv ruff --paths apps/teams/export/tests/test_partial_sync_integration.py
git add apps/teams/export/tests/test_partial_sync_integration.py
git commit -m "test: pin a scoped export against the real serializers and importer"
```

---

## Not in this plan

Named so nobody has to rediscover why:

- **Raising the sync's default page size.** `MAX_LIMIT` is already 1000 (`apps/api/export/views.py:40`) while `sync_team` defaults to 100, so every per-page cost divides by ten for free. It is a one-line default change with no test to write; make it when measuring Phase 1, not as its own task.
- **A denormalised `experiment_id` on `Chat`.** The structural answer if `EXPLAIN` on production-shaped data still picks a hash semi-join for `chat_messages`. An expensive migration on a large table; hold it in reserve until Task 1's indexes are measured.
- **A `?chatbot=` query parameter validated against the allowlist.** It would keep the authorization property and buy per-chatbot cursors plus a one-element `IN` list. Revisit if the cursor seeding in Task 14 turns out not to cover the real re-import cost; `_QUERY_PARAMETERS` (`views.py:190`) is a single list applied to every resource, so it is one entry rather than sixty.
- **A throttle on the export endpoints, and pointing the sync at a read replica.** The source is a live server taking traffic while the sync issues tens of thousands of queries. Check `statement_timeout` applies to these endpoints as a deployment task, not a code change.
- **A per-chatbot "being migrated" badge on chatbot pages.** Follow-up if the team-wide banner turns out to be too vague.
- **Caching `frozen_experiment_q()`.** See deviation 6.
- **`FKTranslationStore.__init__` loading the whole `fk_translation` table into a dict**
  (`apps/teams/export/translation.py:25`). At millions of rows that is hundreds of MB resident on
  the sync host, and it grows across selections because the FK map is deliberately shared. The spec
  names the cost without proposing a fix; Task 7 removes the other startup cost that scaled with the
  migration (`_start_cursor`'s `pk__in=<every committed target pk>`, measured at 6.6s per
  `updated_at_id` model per run), so measure this one before deciding whether an on-demand lookup
  with an LRU is worth the extra SQLite round trips.
