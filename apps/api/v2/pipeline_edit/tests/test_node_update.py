"""PATCH /api/v2/chatbots/{id}/pipeline/nodes/{node_id}/ (#4140)."""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import add_edge, node_url, nodes_url, outgoing_handles


def add_llm_node(client, chatbot, llm) -> str:
    """An LLM node created the way an agent would, so its stored params are the full default set."""
    provider, model = llm
    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]


@pytest.fixture()
def llm_node(client, chatbot, llm):
    return add_llm_node(client, chatbot, llm)


@pytest.mark.django_db()
def test_patch_merges_into_the_stored_params(client, chatbot, llm_node):
    """Only the params sent are touched: a whole-params replace would make editing one field mean
    resending the node, which is what the façade exists to avoid."""
    response = client.patch(node_url(chatbot, llm_node), {"params": {"prompt": "Be terse."}}, format="json")

    assert response.status_code == 200, response.content
    params = Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).params
    assert params["prompt"] == "Be terse."
    assert params["max_history_length"] == 10
    assert params["llm_provider_id"] is not None


@pytest.mark.django_db()
def test_patch_updates_the_label(client, chatbot, llm_node):
    response = client.patch(node_url(chatbot, llm_node), {"label": "Classify"}, format="json")

    assert response.status_code == 200, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).label == "Classify"


@pytest.mark.django_db()
def test_patch_leaves_the_label_alone_when_it_is_not_sent(client, chatbot, llm_node):
    client.patch(node_url(chatbot, llm_node), {"label": "Classify"}, format="json")

    client.patch(node_url(chatbot, llm_node), {"params": {"prompt": "Be terse."}}, format="json")

    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).label == "Classify"


@pytest.mark.django_db()
def test_patch_of_an_unknown_node_is_a_404(client, chatbot):
    response = client.patch(node_url(chatbot, "LLMResponseWithPrompt-nope1"), {"label": "x"}, format="json")

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_patch_refuses_a_param_the_type_does_not_declare(client, chatbot, llm_node):
    response = client.patch(node_url(chatbot, llm_node), {"params": {"tempreture": 0.5}}, format="json")

    assert response.status_code == 400, response.content
    assert "tempreture" in str(response.json())


@pytest.mark.django_db()
def test_patch_refuses_another_teams_resource(client, chatbot, llm_node):
    elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

    response = client.patch(node_url(chatbot, llm_node), {"params": {"llm_provider_id": elsewhere.id}}, format="json")

    assert response.status_code == 400, response.content
    assert "llm_provider_id" in response.json()["params"]


@pytest.mark.django_db()
def test_patch_refuses_to_change_a_nodes_type(client, chatbot, llm_node):
    """A node's type decides what its params mean, so switching it in place would reinterpret
    every stored value. Delete the node and add the other type instead."""
    response = client.patch(node_url(chatbot, llm_node), {"type": "RouterNode"}, format="json")

    assert response.status_code == 400, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).type == "LLMResponseWithPrompt"


@pytest.fixture()
def router(client, chatbot, llm):
    provider, model = llm
    response = client.post(
        nodes_url(chatbot),
        {
            "type": "RouterNode",
            "params": {
                "llm_provider_id": provider.id,
                "llm_provider_model_id": model.id,
                "keywords": ["schedule", "reschedule"],
            },
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]


@pytest.mark.django_db()
def test_editing_router_keywords_regenerates_the_output_handles(client, chatbot, router):
    """Handles are positional (`output_i` serves `keywords[i]`) and the model upper-cases the
    keywords, so the labels read back upper-cased whatever case they were sent in."""
    response = client.patch(
        node_url(chatbot, router), {"params": {"keywords": ["schedule", "reschedule", "cancel"]}}, format="json"
    )

    assert response.status_code == 200, response.content
    assert response.json()["node"]["output_handles"] == [
        {"handle": "output_0", "label": "SCHEDULE"},
        {"handle": "output_1", "label": "RESCHEDULE"},
        {"handle": "output_2", "label": "CANCEL"},
    ]


@pytest.mark.django_db()
def test_a_new_router_branch_shows_up_as_unwired_not_as_an_error(client, chatbot, router):
    """A branch with nowhere to go is a normal state while building, so it is advisory only."""
    response = client.patch(
        node_url(chatbot, router), {"params": {"keywords": ["schedule", "reschedule", "cancel"]}}, format="json"
    )

    handles = response.json()["unwired_handles"][router]
    assert {"handle": "output_2", "label": "CANCEL"} in handles
    assert response.json()["pipeline_errors"]["edge"] == []


@pytest.mark.django_db()
def test_dropping_a_keyword_deletes_the_edge_that_served_it(client, chatbot, llm, router):
    """A branch that no longer exists cannot keep an edge: the builder's own deleteKeyword drops it
    too, and there is no edge endpoint an agent could use to clear up after itself."""
    start = chatbot.pipeline.node_set.get(type="StartNode").flow_id
    add_edge(chatbot.pipeline, start, router)
    dropped = add_edge(chatbot.pipeline, router, add_llm_node(client, chatbot, llm), source_handle="output_1")

    response = client.patch(node_url(chatbot, router), {"params": {"keywords": ["schedule"]}}, format="json")

    assert response.status_code == 200, response.content
    assert dropped not in outgoing_handles(chatbot.pipeline, router)
    assert response.json()["pipeline_errors"]["edge"] == []


@pytest.mark.django_db()
def test_dropping_a_middle_keyword_moves_the_branches_below_it_up(client, chatbot, llm, router):
    """Handles are positional, so dropping RESCHEDULE renumbers CANCEL from `output_2` to
    `output_1`. Old handles are matched to new ones by keyword, so CANCEL's edge follows it down and
    keeps its target -- dropping `output_2` on position alone would have left CANCEL routing to
    RESCHEDULE's target instead."""
    client.patch(
        node_url(chatbot, router), {"params": {"keywords": ["schedule", "reschedule", "cancel"]}}, format="json"
    )
    scheduled, rescheduled, cancelled = (add_llm_node(client, chatbot, llm) for _ in range(3))
    kept = add_edge(chatbot.pipeline, router, scheduled, source_handle="output_0")
    dropped = add_edge(chatbot.pipeline, router, rescheduled, source_handle="output_1")
    moved = add_edge(chatbot.pipeline, router, cancelled, source_handle="output_2")

    response = client.patch(node_url(chatbot, router), {"params": {"keywords": ["schedule", "cancel"]}}, format="json")

    assert response.status_code == 200, response.content
    assert outgoing_handles(chatbot.pipeline, router) == {
        kept: ("output_0", scheduled),
        moved: ("output_1", cancelled),
    }
    assert dropped not in outgoing_handles(chatbot.pipeline, router)


@pytest.mark.django_db()
def test_reordering_keywords_keeps_each_branch_on_its_own_target(client, chatbot, llm, router):
    """Reordering rebinds every handle it moves. Following the keywords means the wiring an agent
    can see -- SCHEDULE goes here, RESCHEDULE goes there -- survives a reorder it did not ask for."""
    scheduled, rescheduled = (add_llm_node(client, chatbot, llm) for _ in range(2))
    first = add_edge(chatbot.pipeline, router, scheduled, source_handle="output_0")
    second = add_edge(chatbot.pipeline, router, rescheduled, source_handle="output_1")

    response = client.patch(
        node_url(chatbot, router), {"params": {"keywords": ["reschedule", "schedule"]}}, format="json"
    )

    assert response.status_code == 200, response.content
    assert outgoing_handles(chatbot.pipeline, router) == {
        first: ("output_1", scheduled),
        second: ("output_0", rescheduled),
    }


@pytest.mark.django_db()
def test_renaming_a_keyword_deletes_its_edge_rather_than_handing_it_over(client, chatbot, llm, router):
    """A rename reads as one branch gone and another new, because nothing in the body says
    otherwise. The old branch's edge goes with it and the new branch comes back unwired, rather than
    quietly inheriting a target nobody chose for it."""
    scheduled, rescheduled = (add_llm_node(client, chatbot, llm) for _ in range(2))
    kept = add_edge(chatbot.pipeline, router, scheduled, source_handle="output_0")
    add_edge(chatbot.pipeline, router, rescheduled, source_handle="output_1")

    response = client.patch(node_url(chatbot, router), {"params": {"keywords": ["schedule", "cancel"]}}, format="json")

    assert response.status_code == 200, response.content
    assert outgoing_handles(chatbot.pipeline, router) == {kept: ("output_0", scheduled)}
    assert {"handle": "output_1", "label": "CANCEL"} in response.json()["unwired_handles"][router]


@pytest.mark.django_db()
def test_an_edge_already_stranded_before_the_edit_is_left_alone(client, chatbot, llm, router):
    """Only the handles this edit removed are followed. An edge on a handle the node never offered
    -- an import, or a builder session -- is still reported and still the agent's to deal with."""
    start = chatbot.pipeline.node_set.get(type="StartNode").flow_id
    add_edge(chatbot.pipeline, start, router)
    stranded = add_edge(chatbot.pipeline, router, add_llm_node(client, chatbot, llm), source_handle="output_7")

    response = client.patch(node_url(chatbot, router), {"label": "Triage"}, format="json")

    assert response.status_code == 200, response.content
    assert response.json()["pipeline_errors"]["edge"] == [stranded]
    assert stranded in outgoing_handles(chatbot.pipeline, router)


@pytest.mark.django_db()
def test_duplicate_keywords_only_drop_the_handles_that_vanished(client, chatbot, llm, router):
    """Duplicate keywords are invalid but still writable, and which edge belongs to which of them is
    a guess -- so a router in that state has its handles followed by position, and only an edge left
    with no handle at all is dropped."""
    client.patch(node_url(chatbot, router), {"params": {"keywords": ["schedule", "schedule"]}}, format="json")
    scheduled, rescheduled = (add_llm_node(client, chatbot, llm) for _ in range(2))
    kept = add_edge(chatbot.pipeline, router, scheduled, source_handle="output_0")
    dropped = add_edge(chatbot.pipeline, router, rescheduled, source_handle="output_1")

    response = client.patch(node_url(chatbot, router), {"params": {"keywords": ["schedule"]}}, format="json")

    assert response.status_code == 200, response.content
    assert outgoing_handles(chatbot.pipeline, router) == {kept: ("output_0", scheduled)}
    assert dropped not in outgoing_handles(chatbot.pipeline, router)


@pytest.mark.django_db()
def test_editing_a_plain_node_leaves_its_edge_alone(client, chatbot, llm_node):
    """Only a node whose handles depend on its params can lose one. A plain node offers the single
    standard output whatever is edited, so nothing about its wiring is this endpoint's business."""
    end = chatbot.pipeline.node_set.get(type="EndNode").flow_id
    edge = add_edge(chatbot.pipeline, llm_node, end)

    response = client.patch(node_url(chatbot, llm_node), {"params": {"prompt": "Be terse."}}, format="json")

    assert response.status_code == 200, response.content
    assert outgoing_handles(chatbot.pipeline, llm_node) == {edge: ("output", end)}


@pytest.mark.django_db()
@pytest.mark.parametrize("node_type", ["StartNode", "EndNode"])
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"label": "Begin here"}, id="label"),
        pytest.param({"params": {"name": "renamed"}}, id="params"),
    ],
)
def test_a_server_managed_node_cannot_be_edited(client, chatbot, node_type, body):
    """Start and End are the server's, whichever half of the body names them -- carving out the
    label would make it two rules instead of one.

    409 rather than 404, and the same answer DELETE gives: the node is there and the address is
    right, so the refusal is about what the node is, not about where it was looked for."""
    node_id = chatbot.pipeline.node_set.get(type=node_type).flow_id
    before = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)

    response = client.patch(node_url(chatbot, node_id), body, format="json")

    assert response.status_code == 409, response.content
    assert "cannot be edited or deleted" in response.json()["detail"]
    after = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
    assert (after.label, after.params) == (before.label, before.params)
