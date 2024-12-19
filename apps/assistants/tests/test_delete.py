import uuid
from unittest.mock import Mock, patch

import pytest
from django.db.models import Q

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.assistants.sync import _get_files_to_delete, delete_openai_files_for_resource
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory(assistant_id="test_id", builtin_tools=["code_interpreter", "file_search"])


@pytest.fixture()
def code_resource(assistant):
    files = FileFactory.create_batch(2, team=assistant.team)
    for f in files:
        f.external_id = str(uuid.uuid4())
        f.external_source = "openai"
        f.save()

    tool_resource = ToolResources.objects.create(tool_type="code_interpreter", assistant=assistant)
    tool_resource.files.set(files)
    return tool_resource


@pytest.mark.django_db()
class TestAssistantDeletion:
    def test_files_to_delete_when_only_referenced_by_one_resource(self, code_resource):
        files_to_delete = list(_get_files_to_delete(code_resource.assistant.team, code_resource.id))
        assert len(files_to_delete) == 2
        assert {f.id for f in files_to_delete} == {f.id for f in code_resource.files.all()}

    def test_files_not_to_delete_when_referenced_by_multiple_resources(self, code_resource):
        all_files = list(code_resource.files.all())
        tool_resource = ToolResources.objects.create(tool_type="file_search", assistant=code_resource.assistant)
        tool_resource.files.set([all_files[0]])

        # only the second file should be deleted
        files_to_delete = list(_get_files_to_delete(code_resource.assistant.team, code_resource.id))
        assert len(files_to_delete) == 1
        assert files_to_delete[0].id == all_files[1].id

        files_to_delete = list(_get_files_to_delete(tool_resource.assistant.team, tool_resource.id))
        assert len(files_to_delete) == 0

    def test_delete_openai_files_for_resource(self, code_resource):
        all_files = list(code_resource.files.all())
        assert all(f.external_id for f in all_files)
        assert all(f.external_source for f in all_files)
        client = Mock()
        delete_openai_files_for_resource(client, code_resource.assistant.team, code_resource)

        assert client.files.delete.call_count == 2
        all_files = list(code_resource.files.all())
        assert not any(f.external_id for f in all_files)
        assert not any(f.external_source for f in all_files)


# assistant.refresh_from_db()


@pytest.mark.django_db()
class TestAssistantArchival:
    def test_archive_assistant(self):
        assistant = OpenAiAssistantFactory()
        assert assistant.is_archived is False
        assistant.archive()
        assert assistant.is_archived is True

    def test_archive_assistant_with_still_exisiting_experiment(self):
        experiment = ExperimentFactory()
        assistant = OpenAiAssistantFactory()
        experiment.assistant = assistant
        experiment.save()

        assistant.archive()
        assert assistant.is_archived is False  # archiving failed

        experiment.archive()
        assistant.archive()
        assert assistant.is_archived is True  # archiving successful

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_versioned_assistant_with_still_exisiting_experiment_and_pipeline(self):
        assistant = OpenAiAssistantFactory()
        v2_assistant = assistant.create_new_version()
        pipeline = PipelineFactory()
        experiment = ExperimentFactory(pipeline=pipeline)
        experiment.assistant = v2_assistant
        experiment.save()

        assistant.archive()
        assert assistant.is_archived is False  # archiving failed
        assert v2_assistant.is_archived is False

        experiment.archive()
        assistant.archive()
        v2_assistant.refresh_from_db()

        assert assistant.is_archived is True  # archiving successful
        assert v2_assistant.is_archived is True

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_get_related_pipeline_node_queryset_with_versions(self):
        assistant = OpenAiAssistantFactory()
        v2_assistant = assistant.create_new_version()
        pipeline = PipelineFactory()
        NodeFactory(type="AssistantNode", pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        exp = ExperimentFactory(pipeline=pipeline)
        exp.assistant = assistant
        exp.save()
        v2_exp = exp.create_new_version()
        NodeFactory(type="AssistantNode", pipeline=v2_exp.pipeline, params={"assistant_id": str(v2_assistant.id)})
        NodeFactory(type="AssistantNode", pipeline=v2_exp.pipeline, params={"assistant_id": str(v2_assistant.id)})
        v2_exp.assistant = v2_assistant
        v2_exp.save()
        assistant.refresh_from_db()
        v2_assistant.refresh_from_db()

        version_query = list(
            map(
                str,
                OpenAiAssistant.objects.filter(Q(id=assistant.id) | Q(working_version__id=assistant.id)).values_list(
                    "id", flat=True
                ),
            )
        )

        assert len(assistant.get_related_pipeline_node_queryset()) == 1
        assert len(v2_assistant.get_related_pipeline_node_queryset()) == 2
        assert len(assistant.get_related_pipeline_node_queryset(query=version_query)) == 3

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_get_related_experiments_queryset_with_versions(self):
        assistant = OpenAiAssistantFactory()
        v2_assistant = assistant.create_new_version()
        exp = ExperimentFactory()
        exp.assistant = assistant
        exp.save()
        exp2 = ExperimentFactory()
        exp2.assistant = assistant
        exp2.save()
        v2_exp = exp.create_new_version()
        v2_exp.assistant = v2_assistant
        v2_exp.save()
        assistant.refresh_from_db()
        v2_assistant.refresh_from_db()

        version_query = list(
            map(
                str,
                OpenAiAssistant.objects.filter(Q(id=assistant.id) | Q(working_version__id=assistant.id)).values_list(
                    "id", flat=True
                ),
            )
        )

        assert len(assistant.get_related_experiments_queryset()) == 2
        assert len(v2_assistant.get_related_experiments_queryset()) == 1
        assert len(assistant.get_related_experiments_queryset(query=version_query)) == 3
