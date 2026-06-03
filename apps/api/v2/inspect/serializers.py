"""Secrets-excluding serializers for the chatbot inspect projection.

Every resource is serialized through an explicit allowlist of fields — never ``__all__`` and
never a denylist (ADR-0027). Adding a field to a model never exposes it here by default.

Encrypted provider ``config`` blobs, signed file-storage URLs, and channel ``extra_data`` are
excluded outright. Provider + model pairs are flattened into a single concept object
(``llm`` / ``voice`` / ``embedding``) by the ``flatten_*`` helpers (ADR-0025); those helpers
operate on already-loaded model instances so the collector can batch-load once and serialize
per reference site.
"""

from rest_framework import serializers

from apps.assistants.models import OpenAiAssistant
from apps.channels.models import ExperimentChannel
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import ConsentForm, SourceMaterial, Survey, SyntheticVoice
from apps.files.models import File
from apps.service_providers.models import EmbeddingProviderModel, LlmProvider, LlmProviderModel, VoiceProvider


class FileSerializer(serializers.ModelSerializer):
    """Identity-lean file view. Excludes the signed ``file`` storage URL, ``summary`` and
    ``metadata`` (the latter two are dropped for size, not secrecy — design D8/Q6)."""

    class Meta:
        model = File
        fields = ["id", "name", "content_type", "content_size", "external_source", "external_id", "purpose"]


class SourceMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceMaterial
        fields = ["id", "topic", "description", "material"]


class ConsentFormSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentForm
        fields = ["id", "name", "consent_text", "capture_identifier", "identifier_label", "identifier_type"]


class SurveySerializer(serializers.ModelSerializer):
    class Meta:
        model = Survey
        fields = ["id", "name", "url", "confirmation_text"]


class AssistantSerializer(serializers.ModelSerializer):
    """``instructions`` and ``assistant_id`` are intentionally exposed (resolved Q4/Q5)."""

    class Meta:
        model = OpenAiAssistant
        fields = ["id", "name", "assistant_id", "instructions", "builtin_tools", "tools", "temperature", "top_p"]


class ProviderSerializer(serializers.Serializer):
    """Minimal provider reference — A plain ``Serializer`` (not ``ModelSerializer``) so it works for
    any provider model (Llm/Voice/Messaging/Auth/Trace) via duck typing. Serializing ``None``
    yields ``None`` when nested; standalone call sites must guard for ``None`` themselves."""

    id = serializers.IntegerField()
    type = serializers.CharField()
    name = serializers.CharField()


class ChannelSerializer(serializers.ModelSerializer):
    """Allowlist ``platform`` + ``name`` + embedded ``messaging_provider`` ref. ``extra_data``
    (freeform auth material) is excluded wholesale (resolved Q8)."""

    messaging_provider = ProviderSerializer(allow_null=True)

    class Meta:
        model = ExperimentChannel
        fields = ["platform", "name", "messaging_provider"]


def serialize_custom_action(action: CustomAction, operation_ids: list[str]) -> dict:
    """Custom action with ``allowed_operations`` reflecting the operations selected at the
    reference site (the node's ``"{action_id}:{operation_id}"`` params) — never the action's full
    operation set — and its OpenAPI schema reduced to the selected operations' path digest
    (resolved Q7 — size, not secrecy). A selected operation no longer present in the action's
    schema resolves to absent. Auth provider is ``{id, type, name}`` only (ADR-0027)."""
    operations_by_id = action.get_operations_by_id()
    operations = [
        operation for operation in (operations_by_id.get(operation_id) for operation_id in operation_ids) if operation
    ]
    return {
        "id": action.id,
        "name": action.name,
        "description": action.description,
        "server_url": action.server_url,
        "allowed_operations": [operation.operation_id for operation in operations],
        # Sorted for a deterministic digest (selection order carries no meaning for paths).
        "api_schema": {"paths": sorted({operation.path for operation in operations})},
        "auth_provider": ProviderSerializer(action.auth_provider).data if action.auth_provider else None,
    }


class LlmProviderSerializer(serializers.ModelSerializer):
    """Provider half of a flattened ``llm``/``embedding`` object — ``{id, name}`` renamed to
    ``provider_id``/``provider_name`` so they can merge with the model half (ADR-0025)."""

    provider_id = serializers.IntegerField(source="id")
    provider_name = serializers.CharField(source="name")

    class Meta:
        model = LlmProvider
        fields = ["provider_id", "provider_name", "type"]


class LlmProviderModelSerializer(serializers.ModelSerializer):
    """Model half of a flattened ``llm`` object; ``name`` is exposed as ``model`` (ADR-0025)."""

    model = serializers.CharField(source="name")

    class Meta:
        model = LlmProviderModel
        fields = ["model", "max_token_limit", "deprecated"]


class VoiceProviderSerializer(serializers.ModelSerializer):
    """Provider half of a flattened ``voice`` object (ADR-0025)."""

    provider_id = serializers.IntegerField(source="id")
    provider_name = serializers.CharField(source="name")

    class Meta:
        model = VoiceProvider
        fields = ["provider_id", "provider_name", "type"]


class SyntheticVoiceSerializer(serializers.ModelSerializer):
    """Voice half of a flattened ``voice`` object; ``name`` is exposed as ``voice_name`` (ADR-0025)."""

    voice_name = serializers.CharField(source="name")

    class Meta:
        model = SyntheticVoice
        fields = ["voice_name", "language", "neural"]


class EmbeddingProviderModelSerializer(serializers.ModelSerializer):
    """Model half of a flattened ``embedding`` object; ``name`` is exposed as ``model`` (ADR-0025)."""

    model = serializers.CharField(source="name")

    class Meta:
        model = EmbeddingProviderModel
        fields = ["model"]


class CollectionSerializer(serializers.ModelSerializer):
    """Collection identity + files; the conditional ``embedding`` key is added by
    ``serialize_collection`` (ADR-0025)."""

    files = FileSerializer(many=True)

    class Meta:
        model = Collection
        fields = ["id", "name", "files"]


def serialize_llm_model(provider, model) -> dict | None:
    """Flatten an ``(LlmProvider, LlmProviderModel)`` pair into one ``llm`` object (ADR-0025)."""
    if provider is None and model is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update(LlmProviderSerializer(provider).data)
    if model is not None:
        result.update(LlmProviderModelSerializer(model).data)
        # provider type falls back to the model's type when the provider is unset
        if provider is None:
            result["type"] = model.type
    return result


def serialize_synthetic_voice(provider, voice) -> dict | None:
    """Flatten a ``(VoiceProvider, SyntheticVoice)`` pair into one ``voice`` object (ADR-0025)."""
    if provider is None and voice is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update(VoiceProviderSerializer(provider).data)
    if voice is not None:
        result.update(SyntheticVoiceSerializer(voice).data)
    return result


def serialize_embedding_model(provider, model) -> dict | None:
    """Flatten a collection's embedding ``(LlmProvider, EmbeddingProviderModel)`` pair (ADR-0025)."""
    if provider is None and model is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update(LlmProviderSerializer(provider).data)
    if model is not None:
        result.update(EmbeddingProviderModelSerializer(model).data)
        if provider is None:
            result["type"] = model.type
    return result


def serialize_collection(collection: Collection, *, with_embedding: bool) -> dict:
    """Serialize a collection two ways (ADR-0025): a media collection (files, no embedding) or an
    indexed/RAG collection (embedding provider+model + files)."""
    data = dict(CollectionSerializer(collection).data)
    if with_embedding:
        data["embedding"] = serialize_embedding_model(collection.llm_provider, collection.embedding_provider_model)
    return data
