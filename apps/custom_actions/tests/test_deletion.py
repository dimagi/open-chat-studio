import uuid

import pytest
from django.core.management import call_command
from django.urls import reverse
from field_audit.models import AuditAction

from apps.custom_actions.models import CustomAction, CustomActionOperation
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.nodes.nodes import EndNode, LLMResponseWithPrompt, StartNode
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


def _force_delete_custom_action(action):
    """Bypass the DeleteCustomAction view guard to simulate data left over from before the guard existed."""
    CustomActionOperation.objects.filter(custom_action=action).delete()
    CustomAction.objects.filter(pk=action.pk).delete(audit_action=AuditAction.AUDIT)


def _pipeline_data_referencing_action(custom_action, llm_provider, llm_provider_model, extra_actions=()):
    llm_node_id = "llm-node"
    custom_actions = [f"{custom_action.id}:weather_get"] + [f"{extra.id}:weather_get" for extra in extra_actions]
    return {
        "edges": [
            {"id": "start->llm", "source": "start", "target": llm_node_id},
            {"id": "llm->end", "source": llm_node_id, "target": "end"},
        ],
        "nodes": [
            {"id": "start", "data": {"id": "start", "type": StartNode.__name__}},
            {
                "id": llm_node_id,
                "data": {
                    "id": llm_node_id,
                    "type": LLMResponseWithPrompt.__name__,
                    "label": "LLM",
                    "params": {
                        "name": llm_node_id,
                        "llm_provider_id": llm_provider.id,
                        "llm_provider_model_id": llm_provider_model.id,
                        "prompt": "You are a helpful assistant.",
                        "custom_actions": custom_actions,
                    },
                },
            },
            {"id": "end", "data": {"id": "end", "type": EndNode.__name__}},
        ],
    }


@pytest.mark.django_db()
def test_delete_view_succeeds_when_no_references(team_with_users, authed_client):
    action = CustomActionFactory.create(team=team_with_users)
    url = reverse("custom_actions:delete", kwargs={"team_slug": team_with_users.slug, "pk": action.pk})

    response = authed_client.delete(url)

    assert response.status_code == 200
    assert not CustomAction.objects.filter(pk=action.pk).exists()


@pytest.mark.django_db()
def test_delete_view_blocks_when_referenced_by_pipeline(team_with_users, authed_client):
    action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)

    pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(action, llm_provider, llm_provider_model),
    )
    assert CustomActionOperation.objects.filter(custom_action=action).exists()

    url = reverse("custom_actions:delete", kwargs={"team_slug": team_with_users.slug, "pk": action.pk})
    response = authed_client.delete(url)

    assert response.status_code == 200
    assert response["HX-Retarget"] == "body"
    assert response["HX-Reswap"] == "beforeend"
    assert CustomAction.objects.filter(pk=action.pk).exists()
    body = response.content.decode()
    assert "referenced-objects-modal" in body
    assert "custom action" in body.lower()
    assert pipeline.name in body


def _strip_custom_actions_from_working_pipeline(working_pipeline):
    """Simulate a user removing the CustomAction from the working pipeline while versioned
    pipelines still reference it. This mirrors the real-world path where the user edits the
    working pipeline (which saves clean) but published versions retain the snapshotted ref."""
    for flow_node in working_pipeline.data.get("nodes", []):
        params = (flow_node.get("data") or {}).get("params") or {}
        if "custom_actions" in params:
            params["custom_actions"] = []
    working_pipeline.save(update_fields=["data"])
    for node in working_pipeline.node_set.filter(type=LLMResponseWithPrompt.__name__):
        node.params["custom_actions"] = []
        node.save(update_fields=["params"])
        CustomActionOperation.objects.filter(node=node).delete()


@pytest.mark.django_db()
def test_delete_view_blocks_when_only_versioned_pipeline_references_action(team_with_users, authed_client):
    """The fix for the case where a published experiment's versioned pipeline still holds a
    CustomActionOperation, but the working pipeline no longer references the action. The old
    implementation collapsed the versioned pipeline to its working version and missed the
    published experiment entirely, silently permitting a delete that would CASCADE-break the
    versioned Node's custom_action_operation row."""
    action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)
    working_pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(action, llm_provider, llm_provider_model),
    )
    pipeline_v1 = working_pipeline.create_new_version()
    assert CustomActionOperation.objects.filter(custom_action=action, node__pipeline=pipeline_v1).exists()

    _strip_custom_actions_from_working_pipeline(working_pipeline)
    assert not CustomActionOperation.objects.filter(custom_action=action, node__pipeline=working_pipeline).exists()

    working_experiment = ExperimentFactory.create(
        team=team_with_users, pipeline=working_pipeline, public_id=uuid.uuid4()
    )
    versioned_experiment = ExperimentFactory.create(
        team=team_with_users,
        name=working_experiment.name,
        pipeline=pipeline_v1,
        working_version=working_experiment,
        version_number=1,
        is_default_version=True,
        public_id=uuid.uuid4(),
    )

    url = reverse("custom_actions:delete", kwargs={"team_slug": team_with_users.slug, "pk": action.pk})
    response = authed_client.delete(url)

    assert response.status_code == 200
    assert response["HX-Retarget"] == "body"
    assert CustomAction.objects.filter(pk=action.pk).exists()
    body = response.content.decode()
    # Specific version (v1) is shown, not the working version name alone, so the user can
    # identify which experiment version is affected.
    assert f"{versioned_experiment.name} (v1)" in body


@pytest.mark.django_db()
def test_delete_view_allows_delete_when_only_orphan_versioned_pipeline_references_action(
    team_with_users, authed_client
):
    """A non-archived versioned pipeline with a CA op but no live experiment using it is
    treated as an orphan — deletion is allowed, matching the ``_is_actively_used`` semantics
    used elsewhere. The ``cleanup_stale_custom_action_refs`` command is expected to mop up
    the resulting stale Node.params/Pipeline.data entries."""
    action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)
    working_pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(action, llm_provider, llm_provider_model),
    )
    working_pipeline.create_new_version()
    _strip_custom_actions_from_working_pipeline(working_pipeline)
    # No Experiment points at the versioned pipeline → orphan.

    url = reverse("custom_actions:delete", kwargs={"team_slug": team_with_users.slug, "pk": action.pk})
    response = authed_client.delete(url)

    assert response.status_code == 200
    assert not CustomAction.objects.filter(pk=action.pk).exists()


@pytest.mark.django_db()
def test_delete_view_does_not_block_when_only_archived_pipeline_references_action(team_with_users, authed_client):
    action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)
    pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(action, llm_provider, llm_provider_model),
    )
    # Archived pipelines/assistants/experiments are acceptable to break on cascade.
    Pipeline.objects.filter(pk=pipeline.pk).update(is_archived=True)

    url = reverse("custom_actions:delete", kwargs={"team_slug": team_with_users.slug, "pk": action.pk})
    response = authed_client.delete(url)

    assert response.status_code == 200
    assert not CustomAction.objects.filter(pk=action.pk).exists()


@pytest.mark.django_db()
def test_cleanup_command_strips_stale_refs_from_node_and_pipeline(team_with_users):
    stale_action = CustomActionFactory.create(team=team_with_users)
    live_action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)
    pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(
            stale_action, llm_provider, llm_provider_model, extra_actions=[live_action]
        ),
    )
    node = pipeline.node_set.get(type=LLMResponseWithPrompt.__name__)
    stale_ref = f"{stale_action.id}:weather_get"
    live_ref = f"{live_action.id}:weather_get"
    assert node.params["custom_actions"] == [stale_ref, live_ref]
    assert CustomActionOperation.objects.filter(node=node, custom_action=live_action).exists()

    # Forcibly break the invariant: delete the CustomActionOperation rows + CustomAction
    # without going through the view (simulates data left over from before the guard existed).
    _force_delete_custom_action(stale_action)

    node.refresh_from_db()
    pipeline.refresh_from_db()
    assert node.params["custom_actions"] == [stale_ref, live_ref]
    assert pipeline.data["nodes"][1]["data"]["params"]["custom_actions"] == [stale_ref, live_ref]

    call_command("cleanup_stale_custom_action_refs")

    node.refresh_from_db()
    pipeline.refresh_from_db()
    assert node.params["custom_actions"] == [live_ref]
    assert pipeline.data["nodes"][1]["data"]["params"]["custom_actions"] == [live_ref]
    # The still-live operation must survive the cleanup.
    assert CustomActionOperation.objects.filter(node=node, custom_action=live_action).exists()


@pytest.mark.django_db()
def test_cleanup_command_is_idempotent_and_dry_run_leaves_data_untouched(team_with_users):
    action = CustomActionFactory.create(team=team_with_users)
    llm_provider = LlmProviderFactory.create(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory.create(team=team_with_users)
    pipeline = PipelineFactory.create(
        team=team_with_users,
        data=_pipeline_data_referencing_action(action, llm_provider, llm_provider_model),
    )
    node = pipeline.node_set.get(type=LLMResponseWithPrompt.__name__)
    stale_ref = f"{action.id}:weather_get"

    _force_delete_custom_action(action)

    call_command("cleanup_stale_custom_action_refs", "--dry-run")
    node.refresh_from_db()
    pipeline.refresh_from_db()
    assert node.params["custom_actions"] == [stale_ref]
    assert pipeline.data["nodes"][1]["data"]["params"]["custom_actions"] == [stale_ref]

    call_command("cleanup_stale_custom_action_refs")
    call_command("cleanup_stale_custom_action_refs")
    node.refresh_from_db()
    pipeline.refresh_from_db()
    assert node.params["custom_actions"] == []
    assert pipeline.data["nodes"][1]["data"]["params"]["custom_actions"] == []
    # Ensure there are no orphan CustomActionOperation rows referencing the deleted action.
    assert not CustomActionOperation.objects.filter(custom_action_id=action.id).exists()
    assert Node.objects.filter(pk=node.pk).exists()
    assert Pipeline.objects.filter(pk=pipeline.pk).exists()
