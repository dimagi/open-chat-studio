import uuid
from unittest.mock import Mock, patch

import pytest

from apps.assistants.models import ToolResources
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
        tool_resource.files.add(all_files[0])

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

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_assistant_succeeds_with_released_related_experiment(self):
        exp_v1 = ExperimentFactory()
        assistant = OpenAiAssistantFactory()
        exp_v2 = exp_v1.create_new_version()
        exp_v2.assistant = assistant
        exp_v2.is_default_version = False
        exp_v2.save()
        assert exp_v2.is_default_version is False
        assert exp_v2.is_working_version is False
        assistant.archive()
        assistant.refresh_from_db()

        assert assistant.is_archived is True  # archiving succeeded

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_assistant_archive_blocked_by_working_related_experiment(self):
        assistant = OpenAiAssistantFactory()
        experiment = ExperimentFactory(assistant=assistant)
        experiment.save()

        assert experiment.is_working_version is True
        assert not assistant.archive()  # archiving blocked

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_assistant_archive_blocked_by_published_related_experiment(self):
        assistant = OpenAiAssistantFactory()
        exp_v1 = ExperimentFactory()
        exp_v2 = exp_v1.create_new_version(make_default=True)
        exp_v2.assistant = assistant
        exp_v2.save()

        assert not assistant.archive()  # archiving blocked

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_assistant_blocked_by_assistant_version_referenced_by_unpublished_related_experiment(self):
        assistant = OpenAiAssistantFactory()
        v2_assistant = assistant.create_new_version()
        experiment = ExperimentFactory(assistant=v2_assistant)
        experiment.save()

        assert experiment.is_working_version is True

        assert not assistant.archive()  # archiving failed
        assert not v2_assistant.archive()  # archiving failed

        experiment.archive()  # first archive related experiment through v2_assistant
        assert assistant.archive()  # archiving successful
        assert v2_assistant.archive()  # archiving successful

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_assistant_succeeds_with_unpublished_related_pipeline(self):
        pipeline = PipelineFactory()
        exp_v1 = ExperimentFactory(pipeline=pipeline)
        exp_v2 = exp_v1.create_new_version()
        assistant = OpenAiAssistantFactory()
        NodeFactory(pipeline=exp_v2.pipeline, type="AssistantNode", params={"assistant_id": assistant.id})
        exp_v2.is_default_version = False
        exp_v2.save()

        assert exp_v2.pipeline.is_working_version is False
        assert assistant.archive()  # archiving successful

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_assistant_fails_with_working_related_versioned_pipeline_and_working_experiment(self):
        pipeline_v1 = PipelineFactory()
        assistant = OpenAiAssistantFactory()
        pipeline_v2 = pipeline_v1.create_new_version()
        NodeFactory(pipeline=pipeline_v2, type="AssistantNode", params={"assistant_id": str(assistant.id)})
        exp_v1 = ExperimentFactory()
        exp_v2 = exp_v1.create_new_version()
        exp_v2.pipeline = pipeline_v2
        exp_v2.is_default_version = True
        exp_v2.save()

        assert pipeline_v2.is_working_version is False
        assert exp_v2.is_default_version is True
        assert exp_v2.is_working_version is False
        assert not assistant.archive()  # archiving failed

        exp_v2.archive()

        assert exp_v2.is_archived is True
        assert assistant.archive()  # archiving successful

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_assistant_fails_with_working_related_pipeline(self):
        pipeline = PipelineFactory()
        assistant = OpenAiAssistantFactory()
        NodeFactory(pipeline=pipeline, type="AssistantNode", params={"assistant_id": str(assistant.id)})

        assert pipeline.is_working_version is True
        assistant.archive()
        assert not assistant.archive()  # archiving failed

        pipeline.archive()

        assert pipeline.is_archived is True
        assert assistant.archive()  # archiving successful
