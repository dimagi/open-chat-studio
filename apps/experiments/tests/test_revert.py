from unittest.mock import Mock, patch

import pytest

from apps.experiments.models import Experiment
from apps.pipelines.models import Node
from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
from apps.pipelines.tests.utils import (
    assistant_node,
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory, SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _build_experiment_with_pipeline():
    experiment = ExperimentFactory.create(name="Original", seed_message="hello")
    team = experiment.team
    assistant = OpenAiAssistantFactory.create(team=team)
    source_material = SourceMaterialFactory.create(team=team)
    provider = LlmProviderFactory.create(team=team)
    provider_model = LlmProviderModelFactory.create(team=team)

    nodes = [
        start_node(),
        assistant_node(str(assistant.id)),
        llm_response_with_prompt_node(
            str(provider.id), str(provider_model.id), source_material_id=str(source_material.id)
        ),
        end_node(),
    ]
    create_pipeline_model(nodes, pipeline=experiment.pipeline)
    # Persist the flow so the published version's node positions/edges are available to flow_data.
    experiment.pipeline.save(update_fields=["data"])
    return experiment, nodes, assistant, source_material


@pytest.mark.django_db()
@patch("apps.assistants.sync.push_assistant_to_openai", Mock())
def test_revert_round_trip_shows_no_changes():
    """Publish v1 → modify → revert to v1 → comparing the working version against v1 shows no changes."""
    experiment, nodes, assistant, source_material = _build_experiment_with_pipeline()
    _, asst, *_ = nodes

    version = experiment.create_new_version(make_default=True)

    # Modify the working experiment's fields and pipeline.
    experiment.name = "Modified"
    experiment.seed_message = "changed"
    experiment.save()
    other_assistant = OpenAiAssistantFactory.create(team=experiment.team)
    asst["params"]["assistant_id"] = str(other_assistant.id)
    create_pipeline_model(nodes, pipeline=experiment.pipeline)

    experiment.revert_to_version(version)

    working = Experiment.objects.get(id=experiment.id)
    working.version_details.compare(version.version_details)
    assert working.version_details.fields_changed is False


@pytest.mark.django_db()
@patch("apps.assistants.sync.push_assistant_to_openai", Mock())
def test_revert_restores_fields_and_remaps_pipeline_to_working_records():
    experiment, nodes, assistant, source_material = _build_experiment_with_pipeline()
    _, asst, *_ = nodes

    version = experiment.create_new_version(make_default=True)

    experiment.name = "Modified"
    experiment.save()
    other_assistant = OpenAiAssistantFactory.create(team=experiment.team)
    asst["params"]["assistant_id"] = str(other_assistant.id)
    create_pipeline_model(nodes, pipeline=experiment.pipeline)

    experiment.revert_to_version(version)
    experiment.refresh_from_db()

    assert experiment.name == "Original"

    # Node params reference the working assistant/source material, not the versioned snapshots.
    asst_node = Node.objects.get(pipeline=experiment.pipeline, type=AssistantNode.__name__)
    llm_node = Node.objects.get(pipeline=experiment.pipeline, type=LLMResponseWithPrompt.__name__)
    assert asst_node.params["assistant_id"] == str(assistant.id)
    assert llm_node.params["source_material_id"] == str(source_material.id)


@pytest.mark.django_db()
@patch("apps.assistants.sync.push_assistant_to_openai", Mock())
def test_revert_is_non_destructive():
    """Version history and the default version must be untouched by a revert."""
    experiment, nodes, _, _ = _build_experiment_with_pipeline()
    version = experiment.create_new_version(make_default=True)

    version_pipeline_id = version.pipeline_id
    experiment.name = "Modified"
    experiment.save()

    experiment.revert_to_version(version)

    version.refresh_from_db()
    assert experiment.versions.count() == 1
    assert version.is_default_version is True
    assert version.pipeline_id == version_pipeline_id


@pytest.mark.django_db()
def test_revert_rejects_non_family_version():
    experiment = ExperimentFactory.create()
    other = ExperimentFactory.create(team=experiment.team)
    other_version = other.create_new_version()

    with pytest.raises(ValueError, match="version of this experiment"):
        experiment.revert_to_version(other_version)
