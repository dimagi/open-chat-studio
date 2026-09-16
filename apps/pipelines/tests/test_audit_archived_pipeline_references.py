from io import StringIO

import pytest
from django.core.management import call_command

from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
class TestAuditArchivedPipelineReferences:
    def test_reports_a_live_node_referencing_an_archived_source_material(self):
        source_material = SourceMaterialFactory.create()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"source_material_id": str(source_material.id)},
        )
        node.update_from_params()
        source_material.is_archived = True
        source_material.save(update_fields=["is_archived"])

        out = StringIO()
        call_command("audit_archived_pipeline_references", stdout=out)

        output = out.getvalue()
        assert node.flow_id in output
        assert "source_material" in output

    def test_reports_nothing_when_no_live_node_references_an_archived_resource(self):
        assistant = OpenAiAssistantFactory.create()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(type="AssistantNode", pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        node.update_from_params()

        out = StringIO()
        call_command("audit_archived_pipeline_references", stdout=out)

        assert "No live nodes reference an already-archived resource" in out.getvalue()

    def test_ignores_an_already_archived_node(self):
        source_material = SourceMaterialFactory.create()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"source_material_id": str(source_material.id)},
        )
        node.update_from_params()
        source_material.is_archived = True
        source_material.save(update_fields=["is_archived"])
        node.is_archived = True
        node.save(update_fields=["is_archived"])

        out = StringIO()
        call_command("audit_archived_pipeline_references", stdout=out)

        assert "No live nodes reference an already-archived resource" in out.getvalue()
