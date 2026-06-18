from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.db import transaction

from apps.pipelines.models import Node
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
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

    def test_synthetic_voice_fk_populated(self):
        voice = SyntheticVoiceFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"synthetic_voice_id": voice.id},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.synthetic_voice_id == voice.id

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

    def test_stale_scalar_fk_id_is_set_not_nulled(self):
        """We no longer pre-check existence before setting the FK: the id from params is
        written straight to the column. Delete guards ensure a live node's params never
        holds a dangling id, and the (deferred) DB FK constraint is the backstop at
        commit — so the code doesn't silently null a non-existent id anymore."""
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": 999999},
        )
        with transaction.atomic():
            node.update_from_params()
            assert node.llm_provider_id == 999999
            # Roll back so the deferred FK violation isn't checked at commit/teardown.
            transaction.set_rollback(True)

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
class TestSetParams:
    """set_params() is the single chokepoint for changing params: it persists the new
    params and re-derives the resource FK mirror, so callers never have to remember to
    call _sync_resource_fk_fields themselves."""

    def test_persists_params(self):
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={"name": "n"})
        node.set_params({"name": "n", "prompt": "hello"})
        node.refresh_from_db()
        assert node.params == {"name": "n", "prompt": "hello"}

    def test_syncs_scalar_fk(self):
        provider = LlmProviderFactory.create()
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={})
        node.set_params({"llm_provider_id": provider.id})
        node.refresh_from_db()
        assert node.llm_provider_id == provider.id

    def test_syncs_m2m(self):
        c1 = CollectionFactory.create()
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={})
        node.set_params({"collection_index_ids": [c1.id]})
        assert set(node.collection_indexes.values_list("id", flat=True)) == {c1.id}

    def test_clears_fk_when_id_removed_from_params(self):
        provider = LlmProviderFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": provider.id},
            llm_provider=provider,
        )
        node.set_params({})
        node.refresh_from_db()
        assert node.llm_provider_id is None


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


@pytest.mark.django_db()
def test_backfill_node_fks_command():
    """The backfill covers every node — working, published versions, and soft-deleted
    (archived) versions — so the FK mirror is complete even for nodes the default manager hides."""
    provider = LlmProviderFactory.create()
    model = LlmProviderModelFactory.create()
    c1 = CollectionFactory.create()

    node_params = {
        "llm_provider_id": provider.id,
        "llm_provider_model_id": model.id,
        "collection_index_ids": [c1.id, 999999],  # 999999 is dangling
    }

    working_pipeline = PipelineFactory.create()
    working_node = NodeFactory.create(type="LLMResponseWithPrompt", params=dict(node_params), pipeline=working_pipeline)
    # A published pipeline version and its (versioned, non-archived) node.
    versioned_pipeline = PipelineFactory.create(working_version=working_pipeline)
    versioned_node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params=dict(node_params),
        pipeline=versioned_pipeline,
        working_version=working_node,
    )
    # A soft-deleted (archived) node version — hidden by Node.objects' default manager.
    archived_node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params=dict(node_params),
        pipeline=versioned_pipeline,
        working_version=working_node,
        is_archived=True,
    )

    nodes = [working_node, versioned_node, archived_node]
    # Simulate pre-backfill state for all of them (get_all() so the archived node is included).
    Node.objects.get_all().filter(pk__in=[n.pk for n in nodes]).update(llm_provider_id=None, llm_provider_model_id=None)
    for node in nodes:
        node.collection_indexes.clear()

    out = StringIO()
    call_command("backfill_node_fks", force=True, stdout=out)

    for node in nodes:
        node.refresh_from_db()
        assert node.llm_provider_id == provider.id, f"node {node.pk} (archived={node.is_archived}) not backfilled"
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
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    node.refresh_from_db()
    assert node.llm_provider_id == provider.id


@pytest.mark.django_db()
def test_backfill_nulls_dangling_scalar_fk():
    """A scalar FK ID in params that references a deleted resource is set to None, not written as-is."""
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": 999999},
    )
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    node.refresh_from_db()
    assert node.llm_provider_id is None


@pytest.mark.django_db()
def test_backfill_skips_dangling_collection_index_id():
    """A collection_index_id in params that references a non-existent collection is not linked."""
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"collection_index_ids": [999999]},
    )
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    assert node.collection_indexes.count() == 0


@pytest.mark.django_db()
def test_backfill_links_all_resources_when_they_exist():
    """When every resource referenced in params exists, all scalar FKs and the M2M are linked."""
    provider = LlmProviderFactory.create()
    model = LlmProviderModelFactory.create()
    source_material = SourceMaterialFactory.create()
    collection = CollectionFactory.create()
    assistant = OpenAiAssistantFactory.create()
    voice = SyntheticVoiceFactory.create()
    index = CollectionFactory.create(is_index=True)

    expected = {
        "llm_provider_id": provider.id,
        "llm_provider_model_id": model.id,
        "source_material_id": source_material.id,
        "collection_id": collection.id,
        "assistant_id": assistant.id,
        "synthetic_voice_id": voice.id,
    }
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={**expected, "collection_index_ids": [index.id]},
    )

    call_command("backfill_node_fks", force=True, stdout=StringIO())
    node.refresh_from_db()

    for attr, resource_id in expected.items():
        assert getattr(node, attr) == resource_id, f"{attr} not linked"
    # Every resource FK on the model is now populated — nothing left dangling.
    assert all(getattr(node, f"{name}_id") is not None for name in Node.resource_fk_fields())
    assert set(node.collection_indexes.values_list("id", flat=True)) == {index.id}


@pytest.mark.django_db()
def test_backfill_keeps_scalar_fk_to_archived_resource():
    """Archiving is a soft-delete: the row still exists, so a scalar FK to it stays linked.

    The versioned resource managers filter is_archived=False, but the FK is still satisfiable,
    so the backfill must not treat an archived-but-existing reference as dangling.
    """
    collection = CollectionFactory.create(is_archived=True)
    source_material = SourceMaterialFactory.create(is_archived=True)
    assistant = OpenAiAssistantFactory.create(is_archived=True)
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={
            "collection_id": collection.id,
            "source_material_id": source_material.id,
            "assistant_id": assistant.id,
        },
    )
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    node.refresh_from_db()
    assert node.collection_id == collection.id
    assert node.source_material_id == source_material.id
    assert node.assistant_id == assistant.id


@pytest.mark.django_db()
def test_backfill_drops_archived_collection_index():
    """The collection_indexes M2M mirrors runtime Collection.objects, which excludes archived rows."""
    valid_index = CollectionFactory.create(is_index=True)
    archived_index = CollectionFactory.create(is_index=True, is_archived=True)
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"collection_index_ids": [valid_index.id, archived_index.id]},
    )
    call_command("backfill_node_fks", force=True, stdout=StringIO())
    assert set(node.collection_indexes.values_list("id", flat=True)) == {valid_index.id}
