from unittest.mock import Mock, patch

import pytest

from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
def test_archive_pipeline_archives_nodes_as_well():
    pipeline = PipelineFactory()
    assert pipeline.node_set.count() > 0
    pipeline.archive()
    assert pipeline.node_set.count() == 0


@pytest.mark.django_db()
class TestNode:
    @pytest.mark.parametrize("versioned_assistant_linked", [True, False])
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_versioning_assistant_node(self, versioned_assistant_linked):
        """
        Versioning an assistant node should version the assistant as well, but only when the linked assistant is not
        already a version
        """
        assistant = OpenAiAssistantFactory()
        if versioned_assistant_linked:
            assistant = assistant.create_new_version()

        pipeline = PipelineFactory()
        NodeFactory(type="AssistantNode", pipeline=pipeline, params={"assistant_id": assistant.id})
        assert pipeline.node_set.filter(type="AssistantNode").exists()

        pipeline.create_new_version()

        original_node = pipeline.node_set.get(type="AssistantNode")
        node_version = pipeline.versions.first().node_set.get(type="AssistantNode")
        assistant_version = assistant if versioned_assistant_linked else assistant.versions.first()

        original_node_assistant_id = original_node.params["assistant_id"]
        node_version_assistant_id = node_version.params["assistant_id"]

        if versioned_assistant_linked:
            assert original_node_assistant_id == node_version_assistant_id == assistant.id
        else:
            assert original_node_assistant_id != node_version_assistant_id
            assert original_node_assistant_id == assistant.id
            assert node_version_assistant_id == assistant_version.id
