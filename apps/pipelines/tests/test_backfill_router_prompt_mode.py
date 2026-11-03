from importlib import import_module
from unittest.mock import patch

import pytest
from django.apps import apps as global_apps
from django.db.models import QuerySet

from apps.pipelines.migrations.utils.backfill_router_prompt_mode import backfill_router_prompt_mode
from apps.pipelines.models import Node
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory

migration_0036 = import_module("apps.pipelines.migrations.0036_backfill_router_prompt_mode")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("params", "expected_mode"),
    [
        pytest.param({"prompt": "Pick one."}, "custom", id="missing-mode-becomes-custom"),
        pytest.param({"prompt_mode": ""}, "custom", id="empty-mode-becomes-custom"),
        pytest.param({"prompt_mode": None}, "custom", id="null-mode-becomes-custom"),
        pytest.param({"prompt_mode": "standard"}, "standard", id="standard-kept"),
        pytest.param({"prompt_mode": "custom"}, "custom", id="custom-kept"),
    ],
)
def test_a_router_without_a_mode_is_stored_as_custom(params, expected_mode):
    node = NodeFactory.create(type="RouterNode", params={"name": "router", **params})

    backfill_router_prompt_mode(Node)

    node.refresh_from_db()
    assert node.params == {"name": "router", **params, "prompt_mode": expected_mode}


@pytest.mark.django_db()
def test_other_node_types_are_left_alone():
    node = NodeFactory.create(type="StaticRouterNode", params={"name": "static"})

    backfill_router_prompt_mode(Node)

    node.refresh_from_db()
    assert node.params == {"name": "static"}


@pytest.mark.django_db()
def test_archived_routers_are_backfilled():
    node = NodeFactory.create(type="RouterNode", params={"name": "router"}, is_archived=True)

    backfill_router_prompt_mode(Node)

    assert Node.objects.get_all().get(id=node.id).params["prompt_mode"] == "custom"


@pytest.mark.django_db()
def test_the_backfill_is_not_reported_as_an_unpublished_change():
    pipeline = PipelineFactory.create()
    node = NodeFactory.create(type="RouterNode", pipeline=pipeline, params={"name": "router", "prompt": "Pick one."})
    pipeline.create_new_version()

    backfill_router_prompt_mode(Node)

    node.refresh_from_db()
    assert not node.compare_with_latest()


@pytest.mark.django_db()
def test_edits_made_while_the_backfill_runs_are_kept():
    switched = NodeFactory.create(type="RouterNode", params={"name": "switched"})
    edited = NodeFactory.create(type="RouterNode", params={"name": "edited"})
    read_ids = QuerySet.values_list

    edits = [
        lambda: Node.objects.filter(id=switched.id).update(params={"name": "switched", "prompt_mode": "standard"}),
        lambda: Node.objects.filter(id=edited.id).update(params={"name": "renamed"}),
    ]

    def read_ids_then_edit(queryset, *args, **kwargs):
        ids = list(read_ids(queryset, *args, **kwargs))
        while edits:
            edits.pop(0)()
        return ids

    with patch.object(QuerySet, "values_list", autospec=True, side_effect=read_ids_then_edit):
        backfill_router_prompt_mode(Node)

    switched.refresh_from_db()
    edited.refresh_from_db()
    assert switched.params == {"name": "switched", "prompt_mode": "standard"}
    assert edited.params == {"name": "renamed", "prompt_mode": "custom"}


@pytest.mark.django_db()
def test_routers_are_backfilled_across_batches():
    nodes = NodeFactory.create_batch(5, type="RouterNode", params={"name": "router"})

    with patch(f"{backfill_router_prompt_mode.__module__}.BATCH_SIZE", 2):
        assert backfill_router_prompt_mode(Node) == 5

    assert all(Node.objects.get(id=node.id).params["prompt_mode"] == "custom" for node in nodes)


@pytest.mark.django_db()
def test_the_migration_backfills_routers():
    node = NodeFactory.create(type="RouterNode", params={"name": "router"})

    migration_0036.backfill_router_prompt_mode(global_apps, None)

    node.refresh_from_db()
    assert node.params["prompt_mode"] == "custom"
