from apps.service_providers.llm_service.default_models import LLM_MODEL_PARAMETERS


def populate_temperature_params(Node, LlmProviderModel):
    """
    This migration prepares the addition of the temperature parameter to the LLM model parameters.

    All nodes that uses LLM models that supports temperature should be updated so that its `llm_model_parameters`
    parameter contains the same value as the existing toplevel `llm_temperature` value.

    For now we're not going to remove the existing `llm_temperature` toplevel parameter, just to be safe.
    It should be OK to leave it there indefinitely, since we wouldn't be using it anymore.
    """

    models_supporting_temperature = []
    for model, param_cls in LLM_MODEL_PARAMETERS.items():
        if "temperature" in param_cls.model_fields:
            models_supporting_temperature.append(model)

    supported_model_ids = list(
        LlmProviderModel.objects.filter(name__in=models_supporting_temperature).values_list("id", flat=True)
    )

    nodes_to_save = []

    # Some ids are saved as strings, others as ints, so we need to check for both
    int_ids = [id for id in supported_model_ids]
    str_ids = [str(id) for id in supported_model_ids]
    for node in Node.objects.filter(params__llm_provider_model_id__in=int_ids + str_ids).iterator():
        params = node.params
        llm_model_params = params.get("llm_model_parameters", {})

        # llm_temperature should exist for all these models, but default to 0.7 just in case
        llm_model_params["temperature"] = params.get("llm_temperature", 0.7)
        params["llm_model_parameters"] = llm_model_params
        nodes_to_save.append(node)

        if len(nodes_to_save) >= 100:
            Node.objects.bulk_update(nodes_to_save, ["params"])
            nodes_to_save = []

    # Final save for any remaining nodes
    if nodes_to_save:
        Node.objects.bulk_update(nodes_to_save, ["params"])
