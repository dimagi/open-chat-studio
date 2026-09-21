import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.custom_actions.models import CustomActionOperation
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.fixture()
def custom_action():
    return CustomActionFactory.create()


@pytest.mark.django_db()
def test_versioning_with_node(custom_action):
    """Test that the custom actions are also versioned when versioning the pipeline"""
    pipeline = PipelineFactory.create()
    node = NodeFactory.create(
        pipeline=pipeline, type="LLMResponseWithPrompt", params={"custom_actions": ["weather_get"]}
    )
    weather_get = CustomActionOperation.objects.create(
        custom_action=custom_action, node=node, operation_id="weather_get"
    )
    pipeline.create_new_version()

    assert node.versions.count() == 1
    assert weather_get.versions.count() == 1
    assert weather_get.versions.first().node == node.versions.first()


@pytest.mark.django_db()
def test_versioning_does_not_read_operations_for_node_types_without_custom_actions():
    """Only node types declaring ``custom_actions`` may read the operation table, one query per node."""
    pipeline = PipelineFactory.create()  # start + end nodes, neither declares custom_actions
    with CaptureQueriesContext(connection) as captured:
        pipeline.create_new_version()

    operation_queries = [
        query["sql"] for query in captured.captured_queries if CustomActionOperation._meta.db_table in query["sql"]
    ]
    assert operation_queries == []


@pytest.mark.django_db()
def test_versioning_copies_operations_that_params_no_longer_list(custom_action):
    """Rows the node holds are versioned even where ``params["custom_actions"]`` has drifted away from them."""
    pipeline = PipelineFactory.create()
    node = NodeFactory.create(pipeline=pipeline, type="LLMResponseWithPrompt", params={"custom_actions": []})
    weather_get = CustomActionOperation.objects.create(
        custom_action=custom_action, node=node, operation_id="weather_get"
    )
    pipeline.create_new_version()

    assert weather_get.versions.count() == 1
    assert weather_get.versions.first().node == node.versions.first()
