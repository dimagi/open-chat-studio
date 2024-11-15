import pytest

from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
def test_archive_pipeline_archives_nodes_as_well():
    pipeline = PipelineFactory()
    assert pipeline.node_set.count() > 0
    pipeline.archive()
    assert pipeline.node_set.count() == 0


@pytest.mark.django_db()
class TestExperiment:
    def test_get_assistant_with_assistant_directly_linked(self):
        assistant = OpenAiAssistantFactory()
        experiment = ExperimentFactory(assistant=assistant)
        assert experiment.get_assistant() == assistant

    @pytest.mark.parametrize("assistant_id_populated", [True, False])
    def test_get_assistant_from_pipeline(self, assistant_id_populated):
        assistant = OpenAiAssistantFactory()
        assistant_id = assistant.id if assistant_id_populated else None
        pipeline = PipelineFactory()
        NodeFactory(pipeline=pipeline, type="AssistantNode", params={"assistant_id": assistant_id})
        experiment = ExperimentFactory(pipeline=pipeline)
        expected_assistant_result = assistant if assistant_id_populated else None
        assert experiment.get_assistant() == expected_assistant_result
