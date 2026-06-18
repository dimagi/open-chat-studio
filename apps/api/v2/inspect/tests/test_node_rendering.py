"""Node rendering tests (DB-backed).

Covers two rules: a node renders the resource keys its type declares (``null`` or ``[]`` when unset)
and omits the rest; and the composite ``voice`` key still renders when only one of its source fields
is present. Nodes are built with their params synced (``update_from_params``) so the FK/M2M mirror
matches production.
"""

import pytest

from apps.api.v2.inspect.serializers import InspectNodeSerializer
from apps.utils.factories.experiment import SyntheticVoiceFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory


def _render(node_type, params):
    node = NodeFactory.create(type=node_type, label=node_type, params=params)
    node.update_from_params()  # sync the FK mirror + custom_action_operations, as production does
    return InspectNodeSerializer(node).data


@pytest.mark.django_db()
def test_start_node_carries_no_resource_keys():
    data = _render("StartNode", {})
    assert set(data) == {"node_id", "type", "label", "params"}


@pytest.mark.django_db()
def test_llm_node_declares_all_keys_with_null_and_empty_when_unset():
    data = _render("LLMResponseWithPrompt", {"prompt": "hi"})
    assert data["params"] == {"prompt": "hi"}
    assert data["llm"] is None
    assert data["voice"] is None
    assert data["source_material"] is None
    assert data["media_collection"] is None
    assert data["custom_actions"] == []
    assert data["indexed_collections"] == []
    assert "assistant" not in data  # LLMResponseWithPrompt does not declare assistant


@pytest.mark.django_db()
@pytest.mark.parametrize(("source", "value", "renamed"), [("max_results", 20, "max_indexed_collection_search_results")])
def test_params_renamed(source, value, renamed):
    """Test parameter renames"""
    data = _render("LLMResponseWithPrompt", {"prompt": "hi", source: value})
    params = data["params"]
    assert source not in params
    assert params[renamed] == 20


@pytest.mark.django_db()
def test_voice_not_dropped_when_only_synthetic_voice_field_set():
    """The ``voice`` key still renders when the node type has only one of its two source fields."""
    provider = VoiceProviderFactory.create()
    voice = SyntheticVoiceFactory.create(name="Rachel", language="English", neural=True, voice_provider=provider)

    data = _render("LLMResponseWithPrompt", {"synthetic_voice_id": voice.id})

    assert data["voice"] == {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "type": provider.type,
        "voice_name": "Rachel",
        "language": "English",
        "neural": True,
    }
