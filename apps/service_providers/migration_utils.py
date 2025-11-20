from django.db import migrations


def populate_temperature_params(Node, LlmProviderModel):
    """
    This migration prepares the addition of the temperature parameter to the LLM model parameters.

    All nodes that uses LLM models that supports temperature should be updated so that its `llm_model_parameters`
    parameter contains the same value as the existing toplevel `llm_temperature` value.

    For now we're not going to remove the existing `llm_temperature` toplevel parameter, just to be safe.
    It should be OK to leave it there indefinitely, since we wouldn't be using it anymore.
    """
    nodes_to_save = []
    node_types = ["LLMResponseWithPrompt", "RouterNode", "ExtractParticipantData", "ExtractStructuredData"]
    for node in Node.objects.filter(type__in=node_types).iterator():
        params = node.params
        temp = params.get("llm_temperature")
        temp = temp if temp is not None else 0.7
        try:
            temp = float(temp)
        except (ValueError, TypeError):
            temp = 0.7
        original_llm_model_params = params.get("llm_model_parameters")
        llm_model_params = original_llm_model_params.copy() if original_llm_model_params else {}
        llm_model_params["temperature"] = temp if temp is not None else 0.7
        if llm_model_params != original_llm_model_params:
            params["llm_model_parameters"] = llm_model_params
            nodes_to_save.append(node)
        if len(nodes_to_save) >= 100:
            Node.objects.bulk_update(nodes_to_save, ["params"])
            nodes_to_save = []

    if nodes_to_save:
        Node.objects.bulk_update(nodes_to_save, ["params"])


def llm_model_migration():
    def _update_llm_models(apps, schema_editor):
        from apps.service_providers.llm_service.default_models import _update_llm_provider_models

        LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
        _update_llm_provider_models(LlmProviderModel)

    return migrations.RunPython(_update_llm_models, migrations.RunPython.noop)


def embedding_model_migration():
    def update_embedding_models(apps, schema_editor):
        from apps.service_providers.llm_service.default_models import _update_embedding_provider_models

        EmbeddingProviderModel = apps.get_model("service_providers", "EmbeddingProviderModel")
        _update_embedding_provider_models(EmbeddingProviderModel)

    return migrations.RunPython(update_embedding_models, migrations.RunPython.noop)
