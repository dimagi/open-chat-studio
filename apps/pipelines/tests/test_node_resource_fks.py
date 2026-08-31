import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.pipelines.models import Node
from apps.pipelines.tests.utils import content_flow_node
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

    def test_stale_scalar_fk_id_is_nulled(self):
        """A scalar FK id in params that references a deleted resource is coerced to null,
        not written straight to the column. Not every SET_NULL resource has a delete guard
        (e.g. LlmProvider), so a stale id can linger in params after the resource is gone;
        resurrecting it would trip the deferred DB FK constraint at commit."""
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": 999999},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.llm_provider_id is None

    def test_scalar_fk_to_archived_resource_is_kept(self):
        """Archiving is a soft-delete: the row still exists, so a scalar FK to it stays
        linked. Existence is checked against _base_manager, not the archive-filtering
        default manager, so a valid reference isn't mistaken for dangling."""
        source_material = SourceMaterialFactory.create(is_archived=True)
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"source_material_id": source_material.id},
        )
        node.update_from_params()
        node.refresh_from_db()
        assert node.source_material_id == source_material.id

    def test_update_nodes_from_data_populates_fks(self):
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create()
        pipeline = PipelineFactory.create()
        node_data = {node.flow_id: None for node in pipeline.node_set.all()}
        node_data["llm1"] = content_flow_node(
            "llm1",
            "LLMResponseWithPrompt",
            label="LLM",
            params={
                "name": "llm1",
                "llm_provider_id": provider.id,
                "llm_provider_model_id": model.id,
                "prompt": "helpful",
                "history_type": "global",
            },
        )
        pipeline.update_nodes_from_data(node_data)
        node = pipeline.node_set.get(flow_id="llm1")
        assert node.llm_provider_id == provider.id
        assert node.llm_provider_model_id == model.id


@pytest.mark.django_db()
class TestFlowNodeReadsResourceFKs:
    """to_flow_node() serves the resource ids from the FK columns, not from the copies in params."""

    def test_scalar_ids_come_from_the_fk_columns(self):
        """Params carries no ids at all, so the served ints can only have come off the columns."""
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"name": "llm"},
            llm_provider=provider,
            llm_provider_model=model,
        )

        params = node.to_flow_node().data.params

        assert params["llm_provider_id"] == provider.id
        assert params["llm_provider_model_id"] == model.id

    def test_reference_to_deleted_resource_reads_as_unset(self):
        """SET_NULL nulls the column when the resource goes, but the id lingers in params. The
        flow node must not resurrect it — an id that no longer resolves reads as unset."""
        provider = LlmProviderFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": provider.id},
            llm_provider=provider,
        )
        provider.delete()
        node.refresh_from_db()
        assert node.params["llm_provider_id"] is not None

        assert node.to_flow_node().data.params["llm_provider_id"] is None

    def test_collection_index_ids_come_from_the_m2m(self):
        """Params carries no ids, so the served list can only have come off the M2M — sorted,
        since the through rows have no ordering of their own."""
        first = CollectionFactory.create(is_index=True)
        second = CollectionFactory.create(is_index=True)
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={"name": "llm"})
        node.collection_indexes.set([second, first])

        assert node.to_flow_node().data.params["collection_index_ids"] == sorted([first.id, second.id])

    def test_deleted_collection_index_reads_as_empty(self):
        """Deleting a Collection cascades the M2M through row away while the id lingers in params.
        The params copy must not be served — the same correction the scalar FKs get."""
        index = CollectionFactory.create(is_index=True)
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"collection_index_ids": [index.id]},
        )
        node.collection_indexes.set([index])
        index.delete()
        node.refresh_from_db()
        assert node.params["collection_index_ids"] != []

        assert node.to_flow_node().data.params["collection_index_ids"] == []

    def test_every_resource_id_is_served_from_the_columns(self):
        """The columns, not params, decide what the flow node reports — so all of them are served,
        including the ones a node type never references."""
        node = NodeFactory.create(type="StartNode", params={"name": "start"})

        assert node.to_flow_node().data.params == {
            "name": "start",
            **{f"{field_name}_id": None for field_name in Node.resource_fk_fields()},
            "collection_index_ids": [],
        }

    def test_stored_params_are_left_alone(self):
        provider = LlmProviderFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"llm_provider_id": str(provider.id)},
            llm_provider=provider,
        )

        node.to_flow_node()

        node.refresh_from_db()
        assert node.params == {"llm_provider_id": str(provider.id)}


@pytest.mark.django_db()
class TestFlowDataReadsResourceFKs:
    @staticmethod
    def _pipeline_with_llm_nodes(count: int, params: dict):
        pipeline = PipelineFactory.create()
        node_data = {node.flow_id: None for node in pipeline.node_set.all()}
        for index in range(count):
            flow_id = f"llm{index}"
            node_data[flow_id] = content_flow_node(
                flow_id,
                "LLMResponseWithPrompt",
                label="LLM",
                params={"name": flow_id, **params},
            )
        pipeline.update_nodes_from_data(node_data)
        pipeline.clear_node_caches()
        return pipeline

    def test_resource_ids_served_from_fk_columns(self):
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create()
        index = CollectionFactory.create(is_index=True)
        pipeline = self._pipeline_with_llm_nodes(
            1,
            {
                "llm_provider_id": str(provider.id),
                "llm_provider_model_id": str(model.id),
                "collection_index_ids": [str(index.id)],
            },
        )

        params = next(node["data"]["params"] for node in pipeline.flow_data["nodes"] if node["id"] == "llm0")

        assert params["llm_provider_id"] == provider.id
        assert params["llm_provider_model_id"] == model.id
        assert params["collection_index_ids"] == [index.id]

    def test_clear_node_caches_re_primes_the_prefetch(self, django_assert_num_queries):
        """The save paths write rows straight to the DB and then rebuild flow_data for the
        response, so the reload has to bring the M2M back with it."""
        index = CollectionFactory.create(is_index=True)
        # _pipeline_with_llm_nodes ends in clear_node_caches(), same as the save paths do.
        pipeline = self._pipeline_with_llm_nodes(3, {"collection_index_ids": [index.id]})

        with django_assert_num_queries(0):
            assert len(pipeline.flow_data["nodes"]) == 5


@pytest.mark.django_db()
class TestEditorEndpointServesResourceFKs:
    """End-to-end over the editor's own endpoint: what the editor posts, what it gets back."""

    @pytest.fixture()
    def authed_client(self, team_with_users):
        client = Client()
        client.force_login(team_with_users.members.first())
        return client

    def _url(self, team_slug, pk):
        return reverse("pipelines:pipeline_data", kwargs={"team_slug": team_slug, "pk": pk})

    def test_deleted_provider_reads_as_unset_after_a_save(self, authed_client, team_with_users):
        provider = LlmProviderFactory.create(team=team_with_users)
        model = LlmProviderModelFactory.create(team=team_with_users)
        pipeline = PipelineFactory.create(team=team_with_users)
        url = self._url(team_with_users.slug, pipeline.id)
        # The editor posts the ids as strings, the form the selects write.
        patch_data = {
            "base_revision": pipeline.edit_revision,
            "nodes": {
                "add": [
                    {
                        "id": "llm-1",
                        "type": "pipelineNode",
                        "position": {"x": 100, "y": 100},
                        "data": {
                            "id": "llm-1",
                            "type": "LLMResponseWithPrompt",
                            "label": "LLM",
                            "params": {
                                "name": "llm-1",
                                "llm_provider_id": str(provider.id),
                                "llm_provider_model_id": str(model.id),
                            },
                        },
                    }
                ],
                "update": [],
                "delete": [],
            },
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(url, data=json.dumps(patch_data), content_type="application/json")
        assert response.status_code == 200
        saved = next(node for node in response.json()["data"]["nodes"] if node["id"] == "llm-1")
        assert saved["data"]["params"]["llm_provider_id"] == provider.id

        provider_id = provider.id  # delete() clears it off the instance
        provider.delete()

        response = authed_client.get(url)
        assert response.status_code == 200
        served = next(node for node in response.json()["pipeline"]["data"]["nodes"] if node["id"] == "llm-1")
        assert served["data"]["params"]["llm_provider_id"] is None
        # The row still carries the stale id; only the read is corrected.
        assert pipeline.node_set.get(flow_id="llm-1").params["llm_provider_id"] == str(provider_id)

    @pytest.mark.parametrize(
        "llm_node_count",
        [
            pytest.param(1, id="one_node"),
            pytest.param(6, id="six_nodes"),
        ],
    )
    def test_editor_load_reads_the_indexes_once(self, authed_client, team_with_users, llm_node_count):
        """The prefetch lives at the endpoint, so serving the graph hits the collection_indexes
        through table once however many nodes there are — not once per node."""
        index = CollectionFactory.create(team=team_with_users, is_index=True)
        pipeline = PipelineFactory.create(team=team_with_users)
        for position in range(llm_node_count):
            NodeFactory.create(
                pipeline=pipeline,
                type="LLMResponseWithPrompt",
                flow_id=f"llm-{position}",
                params={"name": f"llm-{position}", "collection_index_ids": [index.id]},
            ).update_from_params()

        with CaptureQueriesContext(connection) as captured:
            response = authed_client.get(self._url(team_with_users.slug, pipeline.id))
        assert response.status_code == 200

        through_table = Node.collection_indexes.through._meta.db_table
        index_reads = [query for query in captured.captured_queries if through_table in query["sql"]]
        assert len(index_reads) == 1, f"{len(index_reads)} reads of {through_table} for {llm_node_count} nodes"


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
