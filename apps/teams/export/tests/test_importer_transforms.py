"""Pure data->data transforms in the importer engine, tested without a database."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.experiments.models import Experiment
from apps.pipelines.models import Node
from apps.teams.export import seal as seal_mod
from apps.teams.export.importer import (
    UnresolvedForeignKey,
    remap_node_params,
    remap_pipeline_data,
    resolve_fk,
    unseal_secrets,
)


class FakeStore:
    def __init__(self, mapping):
        # mapping: {content_type: {source_pk: target_pk}}
        self._mapping = mapping

    def get_target(self, content_type, source_key):
        return self._mapping.get(content_type, {}).get(source_key)


def test_resolve_fk_translates_known_reference():
    store = FakeStore({"experiments.consentform": {5: 55}})
    field = Experiment._meta.get_field("consent_form")
    assert resolve_fk(field, 5, store) == 55


def test_resolve_fk_returns_none_for_null_source():
    field = Experiment._meta.get_field("consent_form")
    assert resolve_fk(field, None, FakeStore({})) is None


def test_resolve_fk_skips_out_of_scope_target():
    # Node.collection points at documents.collection, which the sync does not copy.
    field = Node._meta.get_field("collection")
    assert resolve_fk(field, 9, FakeStore({})) is None


def test_resolve_fk_raises_for_required_unsynced_reference():
    field = Experiment._meta.get_field("team")  # non-null, in scope
    with pytest.raises(UnresolvedForeignKey):
        resolve_fk(field, 1, FakeStore({}))


def test_resolve_fk_nulls_optional_unsynced_reference():
    field = Experiment._meta.get_field("synthetic_voice")  # nullable, in scope
    assert resolve_fk(field, 1, FakeStore({})) is None


def test_remap_node_params_translates_resource_ids():
    store = FakeStore(
        {
            "service_providers.llmprovider": {7: 700},
            "service_providers.llmprovidermodel": {3: 300},
        }
    )
    params = {"name": "node", "llm_provider_id": 7, "llm_provider_model_id": 3}
    out = remap_node_params(params, store)
    assert out["llm_provider_id"] == 700
    assert out["llm_provider_model_id"] == 300
    assert out["name"] == "node"


def test_remap_node_params_leaves_out_of_scope_refs_untouched():
    params = {"collection_id": 42}  # documents.collection is out of scope
    assert remap_node_params(params, FakeStore({}))["collection_id"] == 42


def test_remap_pipeline_data_strips_node_content_from_old_exports():
    """Old export files embed node content in pipeline data; imported rows must be
    layout-only (ADR-0046). The content itself is imported via the pipelines.node rows."""
    data = {
        "nodes": [
            {
                "id": "a",
                "type": "pipelineNode",
                "position": {"x": 1},
                "data": {"id": "a", "type": "LLMResponseWithPrompt", "params": {"llm_provider_id": 7}},
            }
        ],
        "edges": [],
        "viewport": {"x": 0},
    }
    out = remap_pipeline_data(data, FakeStore({}))
    assert out is not None
    assert out["nodes"] == [{"id": "a", "type": "pipelineNode", "position": {"x": 1}}]
    assert out["viewport"] == {"x": 0}
    assert data["nodes"][0]["data"]["params"]["llm_provider_id"] == 7  # original untouched


def test_remap_pipeline_data_passes_layout_only_data_through():
    data = {"nodes": [{"id": "a", "type": "pipelineNode"}], "edges": []}
    assert remap_pipeline_data(data, FakeStore({})) == data


def test_unseal_secrets_restores_plaintext():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public = seal_mod.load_public_key(public_pem)
    row = {"name": "p", "config": seal_mod.seal({"k": "v"}, public)}
    out = unseal_secrets(row, ["config"], private)
    assert out["config"] == {"k": "v"}
    assert out["name"] == "p"
