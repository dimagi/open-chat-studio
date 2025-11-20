import pytest

from apps.pipelines.models import Node
from apps.service_providers import migration_utils
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory


@pytest.mark.django_db()
def test_populate_temperature_params_migration():
    """
    Test that the migration correctly populates temperature params in llm_model_parameters
    for models that support temperature, but not for models that don't.

    - o4-mini: uses OpenAIReasoningParameters (no temperature support)
    - gpt-4.1-mini: uses BasicParameters (supports temperature)
    """
    # Create the two LLM provider models
    o4_mini_model = LlmProviderModelFactory(
        name="o4-mini",
        type=LlmProviderTypes.openai,
    )
    gpt_mini_model = LlmProviderModelFactory(
        name="gpt-4.1-mini",
        type=LlmProviderTypes.openai,
    )

    # Create a pipeline and nodes for each model
    pipeline = PipelineFactory()

    # Node 1: o4-mini (no temperature support)
    node_o4_mini = Node.objects.create(
        flow_id="node-1",
        type="LLMResponseWithPrompt",
        label="O4 Mini Node",
        pipeline=pipeline,
        params={
            "name": "node-1",
            "llm_provider_model_id": o4_mini_model.id,
            "llm_temperature": 0.5,  # this could have been set by the previous "llm_temperature" node param
            "llm_model_parameters": {},
        },
    )

    # Node 2: gpt-4.1-mini (supports temperature)
    node_gpt_mini = Node.objects.create(
        flow_id="node-2",
        type="LLMResponseWithPrompt",
        label="GPT 4.1 Mini Node",
        pipeline=pipeline,
        params={
            "name": "node-2",
            "llm_provider_model_id": gpt_mini_model.id,
            "llm_temperature": 0.8,
            "llm_model_parameters": {},
        },
    )

    # Node 3: gpt-4.1-mini with string ID (to test both int and string IDs)
    node_gpt_mini_str = Node.objects.create(
        flow_id="node-3",
        type="LLMResponseWithPrompt",
        label="GPT 4.1 Mini Node (String ID)",
        pipeline=pipeline,
        params={
            "name": "node-3",
            "llm_provider_model_id": str(gpt_mini_model.id),
            "llm_temperature": 0.9,
            "llm_model_parameters": {},
        },
    )

    # Apply the migration logic
    migration_utils.populate_temperature_params(Node)

    # Verify the results
    node_o4_mini.refresh_from_db()
    node_gpt_mini.refresh_from_db()
    node_gpt_mini_str.refresh_from_db()

    # o4-mini should NOT have temperature in llm_model_parameters
    # because o4-mini uses OpenAIReasoningParameters which doesn't support temperature
    assert "temperature" not in node_o4_mini.params.get("llm_model_parameters", {}), (
        "o4-mini should not have temperature in llm_model_parameters"
    )
    # But the toplevel llm_temperature should still be there
    assert node_o4_mini.params.get("llm_temperature") == 0.5, "o4-mini should still have toplevel llm_temperature"

    # gpt-4.1-mini should have temperature in llm_model_parameters
    assert "temperature" in node_gpt_mini.params.get("llm_model_parameters", {}), (
        "gpt-4.1-mini should have temperature in llm_model_parameters"
    )
    assert node_gpt_mini.params["llm_model_parameters"]["temperature"] == 0.8, "gpt-4.1-mini temperature should be 0.8"

    # gpt-4.1-mini with string ID should also have temperature
    assert "temperature" in node_gpt_mini_str.params.get("llm_model_parameters", {}), (
        "gpt-4.1-mini (string ID) should have temperature in llm_model_parameters"
    )
    assert node_gpt_mini_str.params["llm_model_parameters"]["temperature"] == 0.9, (
        "gpt-4.1-mini (string ID) temperature should be 0.9"
    )
