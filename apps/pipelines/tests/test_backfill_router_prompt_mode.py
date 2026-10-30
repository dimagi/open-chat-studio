from io import StringIO

import pytest
from django.core.management import call_command

from apps.pipelines.models import Node
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


def _backfill(**options):
    call_command("backfill_router_prompt_mode", stdout=StringIO(), **options)


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

    _backfill()

    node.refresh_from_db()
    assert node.params == {"name": "router", **params, "prompt_mode": expected_mode}


@pytest.mark.django_db()
def test_other_node_types_are_left_alone():
    node = NodeFactory.create(type="StaticRouterNode", params={"name": "static"})

    _backfill()

    node.refresh_from_db()
    assert node.params == {"name": "static"}


@pytest.mark.django_db()
def test_archived_routers_are_backfilled():
    node = NodeFactory.create(type="RouterNode", params={"name": "router"}, is_archived=True)

    _backfill()

    assert Node.objects.get_all().get(id=node.id).params["prompt_mode"] == "custom"


@pytest.mark.django_db()
def test_a_dry_run_changes_nothing():
    node = NodeFactory.create(type="RouterNode", params={"name": "router"})

    _backfill(dry_run=True)

    node.refresh_from_db()
    assert "prompt_mode" not in node.params


@pytest.mark.django_db()
def test_the_backfill_is_not_reported_as_an_unpublished_change():
    pipeline = PipelineFactory.create()
    node = NodeFactory.create(type="RouterNode", pipeline=pipeline, params={"name": "router", "prompt": "Pick one."})
    pipeline.create_new_version()

    _backfill()

    node.refresh_from_db()
    assert not node.compare_with_latest()
