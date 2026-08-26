"""PATCH /api/v2/chatbots/{id}/pipeline/nodes/{node_id}/ (#4140)."""

import pytest

from apps.api.v2.discovery.node_types import get_node_types
from apps.pipelines.models import Node
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import add_edge, node_url, nodes_url, outgoing_handles, stored_node_params


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
def test_patch_drops_a_param_the_type_does_not_declare(client, chatbot, llm_node):
    response = client.patch(node_url(chatbot, llm_node), {"params": {"tempreture": 0.5}}, format="json")

    assert response.status_code == 200, response.content
    assert "tempreture" not in response.json()["node"]["params"]
    assert "tempreture" not in Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).params


@pytest.mark.django_db()
def test_patch_refuses_another_teams_resource(client, chatbot, llm_node):
    elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

    response = client.patch(node_url(chatbot, llm_node), {"params": {"llm_provider_id": elsewhere.id}}, format="json")

    assert response.status_code == 400, response.content
    assert "llm_provider_id" in response.json()["params"]


@pytest.mark.django_db()
def test_patch_accepts_the_teams_own_resource(client, chatbot, llm_node):
    """Guards the guard. The option lists are built in the view and handed to ``plan_update``, so a
    PATCH that names a reference has to actually get them -- handing over an empty set would refuse
    every id, which the refusal above cannot tell apart from working."""
    ours = LlmProviderFactory.create(team=chatbot.team, type="openai")

    response = client.patch(node_url(chatbot, llm_node), {"params": {"llm_provider_id": ours.id}}, format="json")

    assert response.status_code == 200, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).params["llm_provider_id"] == ours.id


@pytest.mark.django_db()
def test_patch_refuses_to_change_a_nodes_type(client, chatbot, llm_node):
    """A node's type decides what its params mean, so switching it in place would reinterpret
    every stored value. Delete the node and add the other type instead."""
    response = client.patch(node_url(chatbot, llm_node), {"type": "RouterNode"}, format="json")

    assert response.status_code == 400, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=llm_node).type == "LLMResponseWithPrompt"


@pytest.fixture()
def deprecated_node(chatbot):
    """A node of a type the API no longer publishes, so it has no schema to describe or check."""
    node = Node.objects.create(
        pipeline=chatbot.pipeline, type="LLMResponse", flow_id="LLMResponse-old1", label="Old", params={"name": "old"}
    )
    chatbot.pipeline.data["nodes"] = []
    chatbot.pipeline.save(update_fields=["data"])
    return node


@pytest.mark.django_db()
def test_a_label_only_edit_to_a_deprecated_type_is_allowed(client, chatbot, deprecated_node):
    """Renaming such a node needs no schema, and a pipeline holding one has to stay editable."""
    response = client.patch(node_url(chatbot, deprecated_node.flow_id), {"label": "Renamed"}, format="json")

    assert response.status_code == 200, response.content
    deprecated_node.refresh_from_db()
    assert deprecated_node.label == "Renamed"


@pytest.mark.django_db()
def test_setting_a_param_on_a_deprecated_type_is_refused(client, chatbot, deprecated_node):
    """The other half of it: the API has no schema to check the value against, so it will not
    pretend to."""
    response = client.patch(node_url(chatbot, deprecated_node.flow_id), {"params": {"name": "new"}}, format="json")

    assert response.status_code == 404, response.content


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
    keywords, so the labels read back upper-cased whatever case they were sent in.

    The added branch has nowhere to go, which is a normal state while building: it comes back under
    `unwired_handles` and not as an error.
    """
    response = client.patch(
        node_url(chatbot, router), {"params": {"keywords": ["schedule", "reschedule", "cancel"]}}, format="json"
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["output_handles"] == [
        {"handle": "output_0", "label": "SCHEDULE"},
        {"handle": "output_1", "label": "RESCHEDULE"},
        {"handle": "output_2", "label": "CANCEL"},
    ]
    assert {"handle": "output_2", "label": "CANCEL"} in body["unwired_handles"][router]
    assert body["pipeline_errors"]["edge"] == []


@pytest.mark.django_db()
def test_dropping_a_middle_keyword_moves_the_branches_below_it_up(client, chatbot, llm, router):
    """Handles are positional, so dropping RESCHEDULE renumbers CANCEL from `output_2` to
    `output_1`. Old handles are matched to new ones by keyword, so CANCEL's edge follows it down and
    keeps its target -- dropping `output_2` on position alone would have left CANCEL routing to
    RESCHEDULE's target instead.

    RESCHEDULE's own edge goes with the branch, the way the builder's `deleteKeyword` drops it:
    there is no edge endpoint an agent could use to clear up after itself, so it must not be left
    behind as a stranded edge either.
    """
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
    assert response.json()["pipeline_errors"]["edge"] == []


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


# ---------------------------------------------------------------------------------------------
# One test per served node type, each PATCHing every param that type declares.
#
# Every node starts from `type` alone, so it holds nothing but the type's defaults, and the PATCH
# then names every param there is: the merge has to end up writing all of them, and the assertion
# that the response reports exactly what was sent says both that it did and -- because a node holds
# a value for every param its type declares -- that nothing was left out of the body.
#
# Written out one test per type, with the whole body as a literal, rather than parametrised over a
# table: the point of these is that you can read what was sent to the endpoint at the place it is
# sent, and a table would put the payloads somewhere else.
# ---------------------------------------------------------------------------------------------

#: The types covered below. Guarded by `test_every_served_type_is_patched_in_full`, which is what
#: fails when a new node type is served and nothing here PATCHes it a full body.
FULL_PAYLOAD_TYPES = {
    "CodeNode",
    "ExtractParticipantData",
    "ExtractStructuredData",
    "LLMResponseWithPrompt",
    "RenderTemplate",
    "RouterNode",
    "SendEmail",
    "StaticRouterNode",
}


def test_every_served_type_is_patched_in_full():
    assert {node_type["type"] for node_type in get_node_types()} == FULL_PAYLOAD_TYPES


def add_bare_node(client, chatbot, node_type: str) -> str:
    """A node created from its type alone, so a PATCH of it has only defaults to overwrite."""
    response = client.post(nodes_url(chatbot), {"type": node_type}, format="json")
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]


@pytest.mark.django_db()
def test_patch_a_code_node_with_every_param(client, chatbot):
    node_id = add_bare_node(client, chatbot, "CodeNode")
    payload = {
        "label": "Trim the answer",
        "params": {
            "name": "trim_answer",
            "code": "def main(input: str, **kwargs) -> str:\n    return input.strip()\n",
            "tag": "trimmed",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Trim the answer"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_patch_a_render_template_node_with_every_param(client, chatbot):
    node_id = add_bare_node(client, chatbot, "RenderTemplate")
    payload = {
        "label": "Format the reply",
        "params": {
            "name": "format_reply",
            "template_string": "Hi {{ participant_data.name }} -- {{ input }}",
            "tag": "formatted",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Format the reply"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_patch_a_send_email_node_with_every_param(client, chatbot):
    """`recipient_list` is stored as sent, spacing included: the model only checks the addresses
    parse, since the field doubles as a Jinja template that is rendered at run time instead."""
    node_id = add_bare_node(client, chatbot, "SendEmail")
    payload = {
        "label": "Email the transcript",
        "params": {
            "name": "email_transcript",
            "recipient_list": "support@example.test, escalations@example.test",
            "subject": "Transcript for {{ participant_details.identifier }}",
            "body": "{{ input }}",
            "tag": "emailed",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Email the transcript"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_patch_an_extract_structured_data_node_with_every_param(client, chatbot, llm):
    provider, model = llm
    node_id = add_bare_node(client, chatbot, "ExtractStructuredData")
    payload = {
        "label": "Pull out the order",
        "params": {
            "name": "extract_order",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.2},
            "data_schema": '{"order_number": "the order the participant is asking about"}',
            "tag": "extracted",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Pull out the order"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_patch_an_extract_participant_data_node_with_every_param(client, chatbot, llm):
    """The same node as `ExtractStructuredData` plus `key_name`, which nests what it extracted under
    that key in the participant's data instead of merging it in at the top level."""
    provider, model = llm
    node_id = add_bare_node(client, chatbot, "ExtractParticipantData")
    payload = {
        "label": "Remember the order",
        "params": {
            "name": "remember_order",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.2},
            "data_schema": '{"order_number": "the order the participant is asking about"}',
            "key_name": "orders",
            "tag": "remembered",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Remember the order"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_patch_a_router_node_with_every_param(client, chatbot, llm):
    """`keywords` come back upper-cased -- the model does that on the way in, so the handles this
    edit regenerates are upper-cased too. A router's prompt sees a narrower set of template
    variables than an LLM node's: `participant_data`, `temp_state` and `session_state`, and nothing
    resource-backed.
    """
    provider, model = llm
    node_id = add_bare_node(client, chatbot, "RouterNode")
    payload = {
        "label": "Triage the request",
        "params": {
            "name": "triage",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.0},
            "prompt": "Route on what {participant_data} is asking for.",
            "keywords": ["schedule", "reschedule", "cancel"],
            "default_keyword_index": 2,
            "tag_output_message": True,
            "history_type": "named",
            "history_name": "triage-history",
            "history_mode": "max_history_length",
            "user_max_token_limit": 4000,
            "max_history_length": 25,
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Triage the request"
    assert body["node"]["params"] == {**payload["params"], "keywords": ["SCHEDULE", "RESCHEDULE", "CANCEL"]}
    assert body["node"]["output_handles"] == [
        {"handle": "output_0", "label": "SCHEDULE"},
        {"handle": "output_1", "label": "RESCHEDULE"},
        {"handle": "output_2", "label": "CANCEL"},
    ]
    assert body["pipeline_errors"]["node"] == {}


@pytest.mark.django_db()
def test_patch_a_static_router_node_with_every_param(client, chatbot):
    """Routes on a key in stored data rather than by asking a model, so it takes no LLM params --
    `data_source` says which of the three data bags to read and `route_key` which key in it."""
    node_id = add_bare_node(client, chatbot, "StaticRouterNode")
    payload = {
        "label": "Route on the plan",
        "params": {
            "name": "route_on_plan",
            "keywords": ["free", "pro"],
            "default_keyword_index": 0,
            "tag_output_message": True,
            "data_source": "session_state",
            "route_key": "plan",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Route on the plan"
    assert body["node"]["params"] == {**payload["params"], "keywords": ["FREE", "PRO"]}
    assert body["node"]["output_handles"] == [
        {"handle": "output_0", "label": "FREE"},
        {"handle": "output_1", "label": "PRO"},
    ]
    assert body["pipeline_errors"]["node"] == {}


@pytest.mark.django_db()
def test_patch_an_llm_node_with_every_param(client, chatbot, llm, llm_node_resources):
    """The type with the most to say, and the only one whose params constrain each other: a resource
    param and its prompt variable each require the other, so `source_material_id` obliges the prompt
    to use `{source_material}`, `collection_id` obliges `{media}`, and so on.

    All in one PATCH, which is the only way this node can reach that state: sending the resource and
    the prompt that names it in separate calls would have each refused on its own.
    """
    provider, model = llm
    support_kb, billing_kb = llm_node_resources.collection_indexes
    node_id = add_bare_node(client, chatbot, "LLMResponseWithPrompt")
    payload = {
        "label": "Answer the question",
        "params": {
            "name": "answer",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.4},
            "prompt": (
                "You are a support agent. Answer from {source_material}, the files in {media} and "
                "whichever of {collection_index_summaries} fits. You are talking to "
                "{participant_data} and it is now {current_datetime}."
            ),
            "history_type": "named",
            "history_name": "support-history",
            "history_mode": "summarize",
            "user_max_token_limit": 8000,
            "max_history_length": 30,
            "source_material_id": llm_node_resources.source_material.id,
            "collection_id": llm_node_resources.media_collection.id,
            "collection_index_ids": [support_kb.id, billing_kb.id],
            "max_results": 5,
            "generate_citations": False,
            "tools": ["update-user-data", "one-off-reminder", "calculator"],
            "custom_actions": [f"{llm_node_resources.custom_action.id}:weather_get"],
            "built_in_tools": ["web-search", "code-execution"],
            "tool_config": {
                "web-search": {
                    "allowed_domains": ["docs.example.test"],
                    "blocked_domains": ["forum.example.test"],
                }
            },
            "synthetic_voice_id": llm_node_resources.synthetic_voice.id,
            "tag": "answered",
        },
    }

    response = client.patch(node_url(chatbot, node_id), payload, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node"]["label"] == "Answer the question"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, node_id).items() >= payload["params"].items()
