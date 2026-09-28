from unittest import mock

import pytest

from apps.pipelines.exceptions import CodeNodeRunError, PipelineBuildError, PipelineNodeRunError
from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.pipelines.tests.utils import create_pipeline_model, end_node, render_template_node, start_node
from apps.utils.factories.pipelines import PipelineFactory

CONFIG_ERROR = {"error": "There are errors in the pipeline configuration. Please correct those before running a test."}


@pytest.mark.django_db()
class TestGetResponseForPipelineTestMessage:
    """The task refuses to run a misconfigured pipeline, and must not refuse a valid one.

    ``Pipeline.validate()`` returns an always-populated report, so the guard has to ask
    ``has_errors()`` — testing the report for truthiness rejects every pipeline.
    """

    def test_valid_pipeline_runs(self, team_with_users):
        user = team_with_users.members.first()
        # Named explicitly: nodes without a ``name`` param all collide on ``None`` and trip the
        # node-name uniqueness check.
        pipeline = create_pipeline_model(
            [start_node(), end_node()], pipeline=PipelineFactory.create(team=team_with_users)
        )
        pipeline.save()

        result = get_response_for_pipeline_test_message(pipeline_id=pipeline.id, message_text="test", user_id=user.id)

        assert "error" not in result
        assert result["messages"][-1] == "test"

    @pytest.mark.parametrize(
        "nodes",
        [
            pytest.param(
                [start_node(), render_template_node(template_string="{{ foo }"), end_node()],
                id="node_with_invalid_params",
            ),
            pytest.param(
                [start_node(), render_template_node(name="dupe"), render_template_node(name="dupe"), end_node()],
                id="duplicate_node_names",
            ),
        ],
    )
    def test_misconfigured_pipeline_is_refused(self, nodes, team_with_users):
        user = team_with_users.members.first()
        pipeline = create_pipeline_model(nodes, pipeline=PipelineFactory.create(team=team_with_users))
        pipeline.save()

        result = get_response_for_pipeline_test_message(pipeline_id=pipeline.id, message_text="test", user_id=user.id)

        assert result == CONFIG_ERROR

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(PipelineBuildError("bad graph"), id="pipeline_build_error"),
            pytest.param(CodeNodeRunError("name 'foo' is not defined"), id="code_node_run_error"),
        ],
    )
    def test_configuration_error_is_returned(self, exc, team_with_users):
        pipeline = self._valid_pipeline(team_with_users)

        with mock.patch("apps.pipelines.tasks.PipelineTestBot.process_input", side_effect=exc):
            result = get_response_for_pipeline_test_message(
                pipeline_id=pipeline.id, message_text="test", user_id=team_with_users.members.first().id
            )

        assert result == {"error": str(exc)}

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(PipelineNodeRunError("ORMRepository not set"), id="pipeline_node_run_error"),
            pytest.param(KeyError("missing"), id="unexpected_error"),
        ],
    )
    def test_other_errors_are_raised(self, exc, team_with_users):
        pipeline = self._valid_pipeline(team_with_users)

        with (
            mock.patch("apps.pipelines.tasks.PipelineTestBot.process_input", side_effect=exc),
            pytest.raises(type(exc)),
        ):
            get_response_for_pipeline_test_message(
                pipeline_id=pipeline.id, message_text="test", user_id=team_with_users.members.first().id
            )

    def _valid_pipeline(self, team):
        pipeline = create_pipeline_model([start_node(), end_node()], pipeline=PipelineFactory.create(team=team))
        pipeline.save()
        return pipeline
