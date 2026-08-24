import pytest

from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.pipelines.tests.utils import code_node, create_pipeline_model, end_node, render_template_node, start_node
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

    def test_code_node_error_is_returned_not_raised(self, team_with_users):
        """An undefined name in the user's own Python compiles fine, so it slips past ``validate()``
        and only surfaces at run time as a ``CodeNodeRunError``. Since that now subclasses
        ``PipelineNodeRunError``, the task's existing handler reports it back to the user instead of
        letting the Celery task crash.
        """
        user = team_with_users.members.first()
        pipeline = create_pipeline_model(
            [start_node(), code_node("def main(input, **kwargs):\n    return undefined_name"), end_node()],
            pipeline=PipelineFactory.create(team=team_with_users),
        )
        pipeline.save()

        result = get_response_for_pipeline_test_message(pipeline_id=pipeline.id, message_text="test", user_id=user.id)

        assert "undefined_name" in result["error"]
