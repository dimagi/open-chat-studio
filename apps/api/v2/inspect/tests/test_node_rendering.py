"""Node rendering tests using a dict-backed fetcher stub — no database, no queries.

Covers two rules: a node renders the resource keys its type declares (``null`` or ``[]`` when
unset) and omits the rest; and the composite keys behave correctly — ``llm`` consumes both of its
source fields, and ``voice`` still renders when only one of its source fields is present.
"""

import dataclasses

from apps.api.v2.inspect.serializers import InspectNodeSerializer
from apps.pipelines.models import Node


@dataclasses.dataclass
class _Node:
    flow_id: str
    type: str
    label: str
    params: dict

    # The serializer asks the node which params its type declares; reuse the real model method
    # (it only reads ``self.type``, no DB) so the stub can't drift from production.
    has_parameter = Node.has_parameter


class _FetcherStub:
    """Resolves any id to ``None`` unless it was seeded in — no database."""

    def __init__(self, **maps):
        self._maps = {kind: dict(values) for kind, values in maps.items()}

    def _get(self, kind, raw_id):
        try:
            return self._maps.get(kind, {}).get(int(raw_id))
        except (TypeError, ValueError):
            return None

    def llm_provider(self, raw_id):
        return self._get("llm_provider", raw_id)

    def llm_provider_model(self, raw_id):
        return self._get("llm_provider_model", raw_id)

    def source_material(self, raw_id):
        return self._get("source_material", raw_id)

    def assistant(self, raw_id):
        return self._get("assistant", raw_id)

    def custom_action(self, raw_id):
        return self._get("custom_action", raw_id)

    def collection(self, raw_id):
        return self._get("collection", raw_id)

    def synthetic_voice(self, raw_id):
        return self._get("synthetic_voice", raw_id)

    def voice_provider(self, raw_id):
        return self._get("voice_provider", raw_id)


def _render(node, fetcher=None):
    return InspectNodeSerializer(node, context={"fetcher": fetcher or _FetcherStub()}).data


def test_start_node_carries_no_resource_keys():
    data = _render(_Node("s", "StartNode", "Start", {}))
    assert set(data) == {"node_id", "type", "label", "params"}


def test_llm_node_declares_all_keys_with_null_and_empty_when_unset():
    data = _render(_Node("a", "LLMResponseWithPrompt", "Answer", {"prompt": "hi"}))
    assert data["node_id"] == "a"
    assert data["params"] == {"prompt": "hi"}
    assert data["llm"] is None
    assert data["voice"] is None
    assert data["source_material"] is None
    assert data["media_collection"] is None
    assert data["custom_actions"] == []
    assert data["indexed_collections"] == []
    assert "assistant" not in data  # LLMResponseWithPrompt does not declare assistant


def test_voice_not_dropped_when_only_synthetic_voice_field_set():
    """The ``voice`` key still renders when the node type has only one of its two source fields."""

    @dataclasses.dataclass
    class _Provider:
        id: int
        name: str
        type: str

    @dataclasses.dataclass
    class _Voice:
        id: int
        name: str
        language: str
        neural: bool
        voice_provider: object

    provider = _Provider(1, "ElevenLabs", "elevenlabs")
    voice = _Voice(5, "Rachel", "English", True, provider)
    fetcher = _FetcherStub(synthetic_voice={5: voice})
    node = _Node("a", "LLMResponseWithPrompt", "Answer", {"synthetic_voice_id": "5"})

    data = _render(node, fetcher)

    assert data["voice"] == {
        "provider_id": 1,
        "provider_name": "ElevenLabs",
        "type": "elevenlabs",
        "voice_name": "Rachel",
        "language": "English",
        "neural": True,
    }
