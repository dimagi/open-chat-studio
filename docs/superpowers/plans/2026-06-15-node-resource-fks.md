# Node Resource FK Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add nullable FK/M2M fields to the `Node` model for all resource references currently stored as bare IDs in `params`. Populate them from `params` via `update_from_params()` and keep them in sync when pipeline versions are created. Provide an idempotent management command to backfill existing nodes.

**Architecture:**
- `params` JSON stays authoritative. FK fields are a derived mirror, always written from `params` — never independently.
- `update_from_params()` (called after every `update_or_create` in `update_nodes_from_data()`) is expanded to sync FK fields from `params`.
- `create_new_version()` already rewrites `params` via the versioning registry (`_NODE_PARAM_SPECS`). After that loop runs, `_sync_resource_fk_fields()` is called on the new version to mirror those updated params into FK fields. `versioning.py` is **not changed**.
- A management command backfills FK fields for existing nodes from their current `params`.

**Not in this PR — follow-up once all nodes have reliable FK fields:**
- Redesign `versioning.py` to drive versioning from FK fields directly (Option B: `VERSIONED_RESOURCE_SPECS` replacing `_NODE_PARAM_SPECS`, methods take `node` instead of `params`). This is only safe after the FK fields are trusted across all nodes including versioned ones.
- Migrate all other params-scanning callsites (inspect API, deletion helpers, admin, etc.) to use FK lookups (see "Follow-up work" table).

**Tech Stack:** Django ORM, pytest, FactoryBoy. No new libraries.

---

## FK fields being added

| `params` key | New `Node` field | Target model | Type | `on_delete` |
|---|---|---|---|---|
| `llm_provider_id` | `llm_provider` | `service_providers.LlmProvider` | FK | `SET_NULL` |
| `llm_provider_model_id` | `llm_provider_model` | `service_providers.LlmProviderModel` | FK | `SET_NULL` |
| `source_material_id` | `source_material` | `experiments.SourceMaterial` | FK | `SET_NULL` |
| `collection_id` | `collection` | `documents.Collection` | FK | `SET_NULL`, `related_name="media_nodes"` |
| `collection_index_ids` | `collection_indexes` | `documents.Collection` | M2M | n/a, `related_name="index_nodes"` |
| `assistant_id` | `assistant` | `assistants.OpenAiAssistant` | FK | `SET_NULL` |
| `synthetic_voice_id` | `synthetic_voice` | `experiments.SyntheticVoice` | FK | `SET_NULL` |

`SET_NULL` on all FKs (including the providers): the FK columns are a derived mirror of `params`, which stays authoritative. Pipeline nodes/versions are **soft-deleted** (archived via `is_archived`, rows persist), so a `PROTECT` FK from a lingering archived version would block deleting the target forever — even when no live pipeline uses it. `SET_NULL` lets the target be deleted and simply nulls the mirror; `params` is untouched.

`custom_actions` is already linked via `CustomActionOperation` M2M — **not changed**.

---

## File structure

| File | Change |
|---|---|
| `apps/pipelines/models.py` | Add 6 FK + 1 M2M fields to `Node`; add `_sync_resource_fk_fields()`; call from `update_from_params()`; call from `create_new_version()` after params versioning |
| `apps/pipelines/migrations/0026_node_resource_fks.py` | Schema migration — new FK/M2M fields |
| `apps/pipelines/management/commands/backfill_node_fks.py` | Idempotent backfill command |
| `apps/pipelines/tests/test_node_resource_fks.py` | New test file — FK sync and versioning |

`apps/pipelines/versioning.py` — **no changes**.

---

## Follow-up work (not in this PR)

**Option B versioning (prerequisite: FK fields are reliably populated on all nodes):**
Redesign `versioning.py` so `version_referenced_record` / `archive_referenced_record` / `resolve_for_display` take the Django `Node` instance and read FK fields directly. Replace `_NODE_PARAM_SPECS` (keyed by node type) with a flat `VERSIONED_RESOURCE_SPECS` dict keyed by field name. This makes versioning data-driven by what FKs are non-null rather than by node type.

**Other params-scanning callsites:**

| Location | Current pattern | FK replacement |
|---|---|---|
| `apps/api/v2/inspect/nodes.py:40` | `RESOURCE_PARAM_FIELDS` dict | Remove; derive from FK fields |
| `apps/api/v2/inspect/resources.py:32` | `iter_resource_refs()` scans params | `select_related`/`prefetch_related` on Node queryset |
| `apps/api/v2/inspect/serializers.py` | `node.params.get(field_id)` in 6 methods | Read `node.llm_provider`, `node.collection`, etc. directly |
| `apps/utils/deletion.py:221` | `params__{field}=id` JSON filters (list variant uses `__contains`, can false-match) | FK filters |
| `apps/assistants/models.py:162` | `params__assistant_id__in=ids` | `Node.objects.filter(assistant__in=ids)` |
| `apps/documents/models.py:251` | Two `get_related_pipelines_queryset*` calls | `self.media_nodes.all() \| self.index_nodes.all()` |
| `apps/service_providers/models.py:253` | `has_related_objects(self, "llm_provider_model_id")` | `self.nodes.exists()` |
| `apps/service_providers/admin.py:42` | `params__llm_provider_model_id=str(obj.id)` | `obj.nodes.all()` |
| `apps/service_providers/usages.py:80` | `get_related_objects(provider, pipeline_param_key=...)` | `provider.nodes.select_related("pipeline")` |
| `apps/service_providers/management/commands/check_llm_model_usage.py:90` | Dual int/str JSON filter | `model.nodes.all()` |
| `apps/pipelines/utils.py:35` | `node.params.get("llm_provider_model_id")` loop | `.values_list("llm_provider_model__max_token_limit", flat=True)` |
| `apps/experiments/models.py:1739` | `values_list("params__assistant_id", flat=True)` | `filter(assistant__isnull=False).values_list("assistant_id", flat=True)` |
| `apps/custom_actions/views.py:147` | `params__assistant_id__in=[str(aid) ...]` | `assistant_id__in=ids` |

---

## Task 1: Write failing FK-sync tests

**Files:**
- Create: `apps/pipelines/tests/test_node_resource_fks.py`

- [ ] **Step 1: Create the test file**

```python
import pytest
from unittest.mock import Mock, patch

from apps.pipelines.models import Node
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.mark.django_db()
class TestNodeResourceFKSync:
    """update_from_params() keeps FK fields in sync with the params JSON."""

    def test_llm_provider_fk_populated(self):
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.llm_provider_id == provider.id
        assert node.llm_provider_model_id == model.id

    def test_source_material_fk_populated(self):
        source_material = SourceMaterialFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"source_material_id": source_material.id},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.source_material_id == source_material.id

    def test_collection_fk_populated(self):
        collection = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_id": collection.id},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.collection_id == collection.id

    def test_collection_indexes_m2m_populated(self):
        c1 = CollectionFactory.create()
        c2 = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_index_ids": [c1.id, c2.id]},
        )
        node.update_from_params()
        assert set(node.collection_indexes.values_list("id", flat=True)) == {c1.id, c2.id}

    def test_collection_indexes_m2m_cleared_when_empty(self):
        c1 = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_index_ids": [c1.id]},
        )
        node.update_from_params()
        assert node.collection_indexes.count() == 1

        node.params["collection_index_ids"] = []
        node.save()
        node.update_from_params()
        assert node.collection_indexes.count() == 0

    def test_fk_fields_null_when_param_absent(self):
        node = NodeFactory.create(type="Passthrough", params={})
        node.update_from_params()
        node.refresh_from_db()
        assert node.llm_provider_id is None
        assert node.llm_provider_model_id is None
        assert node.source_material_id is None
        assert node.collection_id is None
        assert node.assistant_id is None
        assert node.synthetic_voice_id is None
        assert node.collection_indexes.count() == 0

    def test_stale_collection_index_id_is_silently_skipped(self):
        c1 = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_index_ids": [c1.id, 999999]},
        )
        node.update_from_params()
        assert set(node.collection_indexes.values_list("id", flat=True)) == {c1.id}

    def test_update_nodes_from_data_populates_fks(self):
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create()
        pipeline = PipelineFactory.create()
        pipeline.data["nodes"].append(
            {
                "id": "llm1",
                "data": {
                    "id": "llm1",
                    "type": "LLMResponseWithPrompt",
                    "label": "LLM",
                    "params": {
                        "name": "llm1",
                        "llm_provider_id": provider.id,
                        "llm_provider_model_id": model.id,
                        "prompt": "helpful",
                        "history_type": "global",
                    },
                },
            }
        )
        pipeline.save()
        pipeline.update_nodes_from_data()
        node = pipeline.node_set.get(flow_id="llm1")
        assert node.llm_provider_id == provider.id
        assert node.llm_provider_model_id == model.id


@pytest.mark.django_db()
class TestVersioningPopulatesNodeFKFields:
    """After create_new_version(), FK fields on the new version reflect the versioned params."""

    def test_scalar_fk_copied_when_no_versioning_change(self):
        """LIVE_REFERENCE fields (e.g. collection) are copied as-is to the new version."""
        collection = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_id": collection.id},
            collection=collection,
        )
        new_version = node.create_new_version()
        assert new_version.collection_id == collection.id

    def test_collection_indexes_m2m_copied_to_new_version(self):
        c1 = CollectionFactory.create()
        c2 = CollectionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_index_ids": [c1.id, c2.id]},
        )
        node.collection_indexes.set([c1, c2])
        new_version = node.create_new_version()
        assert set(new_version.collection_indexes.values_list("id", flat=True)) == {c1.id, c2.id}
        # original unchanged
        assert set(node.collection_indexes.values_list("id", flat=True)) == {c1.id, c2.id}

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_assistant_fk_repointed_after_versioning(self):
        """NEW_VERSION strategy: after versioning, the FK on the new node version
        points at the versioned assistant, matching the updated params mirror."""
        assistant = OpenAiAssistantFactory.create()
        node = NodeFactory.create(
            type="AssistantNode",
            params={"assistant_id": assistant.id},
            assistant=assistant,
        )
        new_node_version = node.create_new_version()
        # FK must reflect the repointed params
        assert str(new_node_version.assistant_id) == new_node_version.params.get("assistant_id")
        assert new_node_version.assistant.is_a_version
```

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest apps/pipelines/tests/test_node_resource_fks.py -v
```

Expected: `AttributeError` or `FieldError` — FK fields don't exist yet.

---

## Task 2: Add FK fields to Node + schema migration

**Files:**
- Modify: `apps/pipelines/models.py`

- [ ] **Step 1: Add FK and M2M fields to the Node class**

Locate `Node` (around line 316). Add after `is_archived`, before `objects = NodeObjectManager()`:

```python
# Resource FK fields — a derived mirror of the IDs in params, populated by
# update_from_params() and _sync_resource_fk_fields(). params stays authoritative.
# All use SET_NULL: pipeline nodes/versions are soft-deleted (archived, not removed),
# so a lingering reference must never block deleting the target — deleting a provider
# just nulls the mirror; params is untouched.
llm_provider = models.ForeignKey(
    "service_providers.LlmProvider",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="nodes",
)
llm_provider_model = models.ForeignKey(
    "service_providers.LlmProviderModel",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="nodes",
)
source_material = models.ForeignKey(
    "experiments.SourceMaterial",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="nodes",
)
collection = models.ForeignKey(
    "documents.Collection",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="media_nodes",
)
collection_indexes = models.ManyToManyField(
    "documents.Collection",
    blank=True,
    related_name="index_nodes",
)
assistant = models.ForeignKey(
    "assistants.OpenAiAssistant",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="nodes",
)
synthetic_voice = models.ForeignKey(
    "experiments.SyntheticVoice",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="nodes",
)
```

- [ ] **Step 2: Generate and run the schema migration**

```bash
uv run python manage.py makemigrations pipelines --name node_resource_fks
uv run python manage.py migrate pipelines
```

Verify `apps/pipelines/migrations/0026_node_resource_fks.py` adds 6 FK columns and 1 M2M through-table. Expected: `OK`.

- [ ] **Step 3: Lint**

```bash
uv run ruff check apps/pipelines/models.py --fix && uv run ruff format apps/pipelines/models.py
```

---

## Task 3: Implement `_sync_resource_fk_fields` and wire into `update_from_params`

**Files:**
- Modify: `apps/pipelines/models.py`

- [ ] **Step 1: Add `_sync_resource_fk_fields` to Node**

Add directly below `update_from_params`:

```python
def _sync_resource_fk_fields(self):
    """Populate FK/M2M fields from the params JSON.

    Treats 0 and "" as null (frontend can send empty strings for unset selects).
    Stale collection_index_ids that no longer exist are silently skipped.
    Does not save the node itself for scalar FKs if nothing changed.
    """
    from apps.documents.models import Collection  # noqa: PLC0415 - avoid circular import

    params = self.params or {}

    fk_param_map = {
        "llm_provider_id": "llm_provider_id",
        "llm_provider_model_id": "llm_provider_model_id",
        "source_material_id": "source_material_id",
        "collection_id": "collection_id",
        "assistant_id": "assistant_id",
        "synthetic_voice_id": "synthetic_voice_id",
    }
    update_fields = []
    for param_key, field_name in fk_param_map.items():
        value = params.get(param_key) or None
        if getattr(self, field_name) != value:
            setattr(self, field_name, value)
            update_fields.append(field_name)

    if update_fields:
        self.save(update_fields=update_fields)

    index_ids = params.get("collection_index_ids") or []
    self.collection_indexes.set(Collection.objects.filter(id__in=index_ids))
```

- [ ] **Step 2: Call it from `update_from_params`**

Replace the current body of `update_from_params` with:

```python
def update_from_params(self):
    """Callback to do DB related updates pertaining to the node params"""
    from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415 - circular: nodes.nodes→models

    self._sync_resource_fk_fields()

    if self.type == LLMResponseWithPrompt.__name__:
        custom_action_infos = []
        for custom_action_operation in self.params.get("custom_actions") or []:
            custom_action_id, operation_id = custom_action_operation.split(":")
            custom_action_infos.append({"custom_action_id": custom_action_id, "operation_id": operation_id})

        set_custom_actions(self, custom_action_infos)
```

- [ ] **Step 3: Run FK tests — expect all pass**

```bash
uv run pytest apps/pipelines/tests/test_node_resource_fks.py::TestNodeResourceFKSync -v
```

Expected: all 8 tests PASS.

- [ ] **Step 4: Run full pipeline suite for regressions**

```bash
uv run pytest apps/pipelines/ -v --tb=short -q
```

- [ ] **Step 5: Lint**

```bash
uv run ruff check apps/pipelines/models.py --fix && uv run ruff format apps/pipelines/models.py
```

---

## Task 4: Sync FK fields in `create_new_version`

**Files:**
- Modify: `apps/pipelines/models.py` (`Node.create_new_version`, around line 346)

After `create_new_version` runs the existing versioning loop (which rewrites params via `_NODE_PARAM_SPECS`), call `_sync_resource_fk_fields()` on the new version to mirror those updated params into FK fields. Also copy the `collection_indexes` M2M (not copied by Django's field-level copy).

- [ ] **Step 1: Update `create_new_version`**

Current tail of the method:

```python
        if not is_copy:
            for spec in get_versioned_param_specs(self.type):
                spec.version_referenced_record(new_version.params)

        if pipeline is not None:
            new_version.pipeline = pipeline
        new_version.save()
        if self.params.get("custom_actions"):
            self._copy_custom_action_operations_to_new_version(new_node=new_version, is_copy=is_copy)

        return new_version
```

Replace with:

```python
        if not is_copy:
            for spec in get_versioned_param_specs(self.type):
                spec.version_referenced_record(new_version.params)

        if pipeline is not None:
            new_version.pipeline = pipeline
        new_version.save()
        if self.params.get("custom_actions"):
            self._copy_custom_action_operations_to_new_version(new_node=new_version, is_copy=is_copy)
        # Mirror any param IDs (including those repointed by versioning above) into FK fields.
        # Also copies the collection_indexes M2M, which is not included in field-level copy.
        new_version._sync_resource_fk_fields()

        return new_version
```

- [ ] **Step 2: Run the versioning FK tests**

```bash
uv run pytest apps/pipelines/tests/test_node_resource_fks.py::TestVersioningPopulatesNodeFKFields -v
```

Expected: all 3 tests PASS.

- [ ] **Step 3: Run the full versioning test suite**

```bash
uv run pytest apps/pipelines/tests/test_models.py -v --tb=short
```

Expected: all PASS.

- [ ] **Step 4: Lint**

```bash
uv run ruff check apps/pipelines/models.py --fix && uv run ruff format apps/pipelines/models.py
```

---

## Task 5: Management command to backfill existing nodes

**Files:**
- Create: `apps/pipelines/management/commands/backfill_node_fks.py`

- [ ] **Step 1: Ensure the management directory exists**

```bash
ls apps/pipelines/management/commands/ 2>/dev/null || (mkdir -p apps/pipelines/management/commands && touch apps/pipelines/management/__init__.py apps/pipelines/management/commands/__init__.py)
```

- [ ] **Step 2: Create the command**

```python
import logging

from django.core.management.base import BaseCommand

from apps.pipelines.models import Node

logger = logging.getLogger(__name__)

FK_PARAM_MAP = {
    "llm_provider_id": "llm_provider_id",
    "llm_provider_model_id": "llm_provider_model_id",
    "source_material_id": "source_material_id",
    "collection_id": "collection_id",
    "assistant_id": "assistant_id",
    "synthetic_voice_id": "synthetic_voice_id",
}


class Command(BaseCommand):
    help = "Backfill resource FK fields on Node from params JSON. Idempotent."

    def handle(self, *args, **options):
        from apps.documents.models import Collection  # noqa: PLC0415

        total = Node.objects.count()
        self.stdout.write(f"Backfilling FK fields for {total} nodes...")
        updated = skipped = 0

        for node in Node.objects.iterator():
            params = node.params or {}
            update_fields = []

            for param_key, field_name in FK_PARAM_MAP.items():
                value = params.get(param_key) or None
                if getattr(node, field_name) != value:
                    setattr(node, field_name, value)
                    update_fields.append(field_name)

            if update_fields:
                try:
                    node.save(update_fields=update_fields)
                    updated += 1
                except Exception:
                    logger.exception("Failed FK backfill for node pk=%s flow_id=%s", node.pk, node.flow_id)
                    skipped += 1
                    continue

            index_ids = params.get("collection_index_ids") or []
            existing_ids = set(Collection.objects.filter(id__in=index_ids).values_list("id", flat=True))
            dangling = set(index_ids) - existing_ids
            if dangling:
                logger.warning("Node pk=%s: skipping dangling collection_index_ids %s", node.pk, dangling)
            node.collection_indexes.set(existing_ids)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated: {updated}, already up-to-date: {total - updated - skipped}, errors: {skipped}"
            )
        )
```

- [ ] **Step 3: Verify the command runs on the dev database**

```bash
uv run python manage.py backfill_node_fks
```

Expected: `Done. Updated: N, already up-to-date: M, errors: 0`

- [ ] **Step 4: Add command tests**

Add to `apps/pipelines/tests/test_node_resource_fks.py`:

```python
from io import StringIO
from django.core.management import call_command


@pytest.mark.django_db()
def test_backfill_node_fks_command():
    provider = LlmProviderFactory.create()
    model = LlmProviderModelFactory.create()
    c1 = CollectionFactory.create()

    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "collection_index_ids": [c1.id, 999999],  # 999999 is dangling
        },
    )
    # Simulate pre-backfill state
    Node.objects.filter(pk=node.pk).update(llm_provider_id=None, llm_provider_model_id=None)
    node.collection_indexes.clear()

    out = StringIO()
    call_command("backfill_node_fks", stdout=out)

    node.refresh_from_db()
    assert node.llm_provider_id == provider.id
    assert node.llm_provider_model_id == model.id
    assert set(node.collection_indexes.values_list("id", flat=True)) == {c1.id}
    assert "Done." in out.getvalue()


@pytest.mark.django_db()
def test_backfill_node_fks_command_is_idempotent():
    provider = LlmProviderFactory.create()
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": provider.id},
    )
    call_command("backfill_node_fks", stdout=StringIO())
    call_command("backfill_node_fks", stdout=StringIO())
    node.refresh_from_db()
    assert node.llm_provider_id == provider.id
```

- [ ] **Step 5: Run all tests**

```bash
uv run pytest apps/pipelines/tests/test_node_resource_fks.py -v
```

Expected: all PASS.

---

## Task 6: Final verification

- [ ] **Step 1: Full test suite for affected apps**

```bash
uv run pytest apps/pipelines/ apps/api/ -v --tb=short -q
```

- [ ] **Step 2: Type-check**

```bash
uv run ty check apps/pipelines/models.py
```

- [ ] **Step 3: Lint all changed files**

```bash
uv run ruff check apps/pipelines/models.py apps/pipelines/tests/test_node_resource_fks.py apps/pipelines/management/commands/backfill_node_fks.py --fix
uv run ruff format apps/pipelines/models.py apps/pipelines/tests/test_node_resource_fks.py apps/pipelines/management/commands/backfill_node_fks.py
```
