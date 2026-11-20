import pytest
from django.core.management import call_command

from apps.pipelines.models import NodeResourceCutoverState
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_cutover_command_supports_validation_cutover_and_rollback():
    """Validate the staged cutover, preserve relations, and restore JSON for rollback."""
    team = TeamFactory.create()
    pipeline = PipelineFactory.create(team=team)
    provider = LlmProviderFactory.create(team=team)
    model = LlmProviderModelFactory.create(team=team)
    node = NodeFactory.create(
        pipeline=pipeline,
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
    )
    state = NodeResourceCutoverState.objects.get(pk=1)

    call_command("node_resource_params_compatibility", "prepare")
    state.refresh_from_db()
    assert state.legacy_json_compatibility_enabled

    call_command("node_resource_params_compatibility", "cutover", dry_run=True)
    node.refresh_from_db()
    assert node.params["llm_provider_id"] == provider.id

    call_command("node_resource_params_compatibility", "cutover")
    node.refresh_from_db()
    state.refresh_from_db()
    assert "llm_provider_id" not in node.params
    assert "llm_provider_model_id" not in node.params
    assert node.llm_provider_id == provider.id
    assert node.llm_provider_model_id == model.id
    assert not state.legacy_json_compatibility_enabled
    assert state.cleanup_completed_at is not None

    call_command("node_resource_params_compatibility", "prepare")
    state.refresh_from_db()
    assert not state.legacy_json_compatibility_enabled

    call_command("node_resource_params_compatibility", "restore")
    node.refresh_from_db()
    state.refresh_from_db()
    assert node.params["llm_provider_id"] == provider.id
    assert node.params["llm_provider_model_id"] == model.id
    assert state.legacy_json_compatibility_enabled
    assert state.cleanup_completed_at is None
