from importlib import import_module

import pytest
from django.apps import apps as global_apps

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
def test_the_migration_backfills_routers():
    node = NodeFactory.create(type="RouterNode", params={"name": "router"})

    migration_0036.backfill_router_prompt_mode(global_apps, None)

    node.refresh_from_db()
    assert node.params["prompt_mode"] == "custom"
