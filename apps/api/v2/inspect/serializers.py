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
from apps.experiments.models import ConsentForm, SourceMaterial, Survey
from apps.files.models import File


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


class ChannelSerializer(serializers.ModelSerializer):
    """Allowlist ``platform`` + ``name`` only. ``extra_data`` (freeform auth material) is excluded
    wholesale (resolved Q8); ``messaging_provider`` is embedded separately by the builder."""

    class Meta:
        model = ExperimentChannel
        fields = ["platform", "name"]


def serialize_custom_action(action: CustomAction) -> dict:
    """Custom action with its OpenAPI schema reduced to a path digest (resolved Q7 — size, not
    secrecy) and its auth provider as ``{id, type, name}`` only (config excluded, ADR-0027)."""
    schema = action.api_schema or {}
    # Sorted for a deterministic digest (the underlying paths dict has no meaningful order).
    paths = sorted(schema.get("paths", {}).keys()) if isinstance(schema, dict) else []
    return {
        "id": action.id,
        "name": action.name,
        "description": action.description,
        "server_url": action.server_url,
        "allowed_operations": list(action.allowed_operations or []),
        "api_schema": {"paths": paths},
        "auth_provider": provider_ref(action.auth_provider),
    }


def provider_ref(provider) -> dict | None:
    """Minimal provider reference — ``{id, type, name}``. The encrypted ``config`` is never read,
    so it cannot leak (ADR-0027). Works for any provider model (Llm/Voice/Messaging/Auth/Trace)."""
    if provider is None:
        return None
    return {"id": provider.id, "type": provider.type, "name": provider.name}


def flatten_llm(provider, model) -> dict | None:
    """Flatten an ``(LlmProvider, LlmProviderModel)`` pair into one ``llm`` object (ADR-0025)."""
    if provider is None and model is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update({"provider_id": provider.id, "provider_name": provider.name, "type": provider.type})
    if model is not None:
        result.update(
            {
                "model": model.name,
                "max_token_limit": model.max_token_limit,
                "deprecated": model.deprecated,
                # provider type falls back to the model's type when the provider is unset
                "type": result["type"] if provider is not None else model.type,
            }
        )
    return result


def flatten_voice(provider, voice) -> dict | None:
    """Flatten a ``(VoiceProvider, SyntheticVoice)`` pair into one ``voice`` object (ADR-0025)."""
    if provider is None and voice is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update({"provider_id": provider.id, "provider_name": provider.name, "type": provider.type})
    if voice is not None:
        result.update({"voice_name": voice.name, "language": voice.language, "neural": voice.neural})
    return result


def flatten_embedding(provider, model) -> dict | None:
    """Flatten a collection's embedding ``(LlmProvider, EmbeddingProviderModel)`` pair (ADR-0025)."""
    if provider is None and model is None:
        return None
    result = {"provider_id": None, "provider_name": None, "type": None}
    if provider is not None:
        result.update({"provider_id": provider.id, "provider_name": provider.name, "type": provider.type})
    if model is not None:
        result["model"] = model.name
        if provider is None:
            result["type"] = model.type
    return result


def serialize_collection(collection: Collection, *, with_embedding: bool) -> dict:
    """Serialize a collection two ways (ADR-0025): a media collection (files, no embedding) or an
    indexed/RAG collection (embedding provider+model + files)."""
    data = {
        "id": collection.id,
        "name": collection.name,
        "files": FileSerializer(collection.files.all(), many=True).data,
    }
    if with_embedding:
        data["embedding"] = flatten_embedding(collection.llm_provider, collection.embedding_provider_model)
    return data
