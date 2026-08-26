"""POST /api/v2/chatbots/{id}/pipeline/nodes/ (#4140, spec §6.2).

The rule the refusals encode: a structurally-sound node always persists, even when it is
semantically incomplete, so an agent can build a graph a piece at a time. What does *not* persist is
a request naming something that does not exist — a node type, or a resource id.
"""

from unittest.mock import Mock, patch

import pytest

from apps.api.v2.discovery.node_types import get_node_types
from apps.pipelines.models import Node
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import nodes_url, stored_node_params


@pytest.mark.django_db()
def test_create_adds_a_node_with_a_server_assigned_id(client, chatbot, llm):
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
    node_id = response.json()["node"]["node_id"]
    assert node_id.startswith("LLMResponseWithPrompt-")
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()


@pytest.mark.django_db()
def test_create_fills_in_the_node_types_defaults(client, chatbot):
    """`type` alone has to be enough to add a node.

    The node class is the only place the defaults live, and `update_nodes_from_data` stores params
    verbatim, so unless they are materialized here the node reads back from /inspect/ as the handful
    of keys the client happened to send. `name` is required on every type and has no default, so the
    server supplies the node id, as the UI builder does; the label defaults to the type's own.
    """
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 201, response.content
    node_id = response.json()["node"]["node_id"]
    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id)
    assert node.params["history_type"] == "global"
    assert node.params["max_history_length"] == 10
    assert node.params["name"] == node_id
    assert response.json()["node"]["label"] == "LLM"


@pytest.mark.django_db()
def test_create_takes_a_label_and_a_name_over_the_defaults(client, chatbot):
    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "label": "Classify", "params": {"name": "classifier"}},
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Classify"
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=body["node"]["node_id"]).params["name"] == "classifier"


@pytest.mark.django_db()
def test_a_colliding_node_id_is_redrawn(client, chatbot):
    """``apply_pipeline_patch`` treats an add whose id already exists as a no-op, so a clash would
    answer 201 while describing the node that was already there."""
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    taken = created.json()["node"]["node_id"]
    collision = taken.removeprefix("CodeNode-")

    # The first draw repeats the id already in the graph, the second is free.
    draws = [Mock(hex=f"{collision}0000000"), Mock(hex="abcde" + "0" * 27)]
    with patch("apps.api.v2.pipeline_edit.graph_editor.uuid4", side_effect=draws):
        response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

    assert response.status_code == 201, response.content
    assert response.json()["node"]["node_id"] == "CodeNode-abcde"
    assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2


@pytest.mark.django_db()
def test_an_id_source_that_only_collides_still_answers(client, chatbot):
    """The redraw is bounded: an id source stuck on one value must fall back to a longer id rather
    than spin inside the pipeline row lock until the request times out."""
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    collision = created.json()["node"]["node_id"].removeprefix("CodeNode-")

    with patch("apps.api.v2.pipeline_edit.graph_editor.uuid4", return_value=Mock(hex=f"{collision}{'0' * 27}")):
        response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

    assert response.status_code == 201, response.content
    assert response.json()["node"]["node_id"] == f"CodeNode-{collision}{'0' * 27}"
    assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2


@pytest.mark.django_db()
def test_create_parks_the_node_clear_of_the_existing_ones(client, chatbot):
    """Nothing wires a new node yet, so there is no source to place it beside; it is parked a node's
    width right of every node already on the canvas -- bar the output, which the next tests cover."""
    chatbot.pipeline.node_set.update(position_x=400, position_y=50)

    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
    assert (node.position_x, node.position_y) == (800, 200)


@pytest.mark.django_db()
def test_create_leaves_the_output_node_where_it_is_when_the_new_node_lands_short_of_it(client, chatbot):
    """A layout someone arranged in the UI builder is not rearranged for the sake of it: a new node
    that fits to the output's left leaves it alone."""
    _place(chatbot.pipeline, start=100, end=800)

    client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    end = _end_node(chatbot.pipeline)
    assert (end.position_x, end.position_y) == (800, 200)


@pytest.mark.django_db()
def test_create_moves_the_output_node_clear_of_a_new_node_that_overtakes_it(client, chatbot):
    """A node level with or past the output would read as running after the end of the pipeline, so
    the output is moved a node's width beyond it: it is always the last node in the x direction."""
    _place(chatbot.pipeline, start=100, end=300)

    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    node = Node.objects.get(pipeline=chatbot.pipeline, flow_id=response.json()["node"]["node_id"])
    end = _end_node(chatbot.pipeline)
    assert (node.position_x, end.position_x, end.position_y) == (500, 900, 200)


@pytest.mark.django_db()
def test_create_moves_the_output_node_without_rewriting_what_it_holds(client, chatbot):
    """Moving the output means writing its row, and the graph's copy of a node's params carries the
    resource-id mirror `to_flow_node` merges in -- which the move must not store on the row."""
    _place(chatbot.pipeline, start=100, end=300)

    client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    end = _end_node(chatbot.pipeline)
    assert end.params == {"name": "end"}
    assert end.label == ""


@pytest.mark.django_db()
def test_an_unknown_node_type_is_refused(client, chatbot):
    """404, the same answer /pipeline/nodes/{type}/ gives, with the valid types alongside it."""
    response = client.post(nodes_url(chatbot), {"type": "Frobnicator"}, format="json")

    assert response.status_code == 404, response.content
    assert "LLMResponseWithPrompt" in response.json()["valid_types"]
    assert not chatbot.pipeline.node_set.filter(type="Frobnicator").exists()


@pytest.mark.django_db()
def test_a_server_managed_node_type_is_refused(client, chatbot):
    """Start and End are created with the pipeline and are not something a client may add — the
    same refusal /pipeline/nodes/{type}/ already gives for them."""
    response = client.post(nodes_url(chatbot), {"type": "StartNode"}, format="json")

    assert response.status_code == 404, response.content
    assert "managed by the server" in response.json()["detail"]


@pytest.mark.django_db()
def test_a_body_with_no_type_is_refused(client, chatbot):
    response = client.post(nodes_url(chatbot), {"params": {"name": "orphan"}}, format="json")

    assert response.status_code == 400, response.content
    assert "type" in response.json()


@pytest.mark.django_db()
def test_a_client_supplied_node_id_is_refused(client, chatbot):
    """Ids are the server's to assign (W5): honouring a client's would let two nodes collide."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "node_id": "mine-1"}, format="json")

    assert response.status_code == 400, response.content
    assert "server" in str(response.json()["node_id"]).lower()
    assert not Node.objects.filter(pipeline=chatbot.pipeline, flow_id="mine-1").exists()


@pytest.mark.django_db()
def test_an_unrecognised_body_key_is_refused(client, chatbot):
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "colour": "red"}, format="json")

    assert response.status_code == 400, response.content
    assert "colour" in response.json()


@pytest.mark.django_db()
def test_an_unrecognised_param_is_dropped(client, chatbot):
    response = client.post(
        nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"tempreture": 0.5}}, format="json"
    )

    assert response.status_code == 201, response.content
    node_id = response.json()["node"]["node_id"]
    assert "tempreture" not in response.json()["node"]["params"]
    assert "tempreture" not in Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params


@pytest.mark.django_db()
def test_a_missing_required_param_persists_and_is_reported(client, chatbot):
    """Lenient on structure: the node lands so the next call can fill it in, and the gap shows up
    in the errors report the publish gate rejects on."""
    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    node_id = body["node"]["node_id"]
    assert Node.objects.filter(pipeline=chatbot.pipeline, flow_id=node_id).exists()
    assert body["pipeline_valid"] is False
    assert "llm_provider_id" in body["pipeline_errors"]["node"][node_id]


@pytest.mark.django_db()
def test_a_reference_to_another_teams_resource_is_refused(client, chatbot):
    """Indistinguishable from a nonexistent id on purpose: telling the two apart would answer
    whether the id exists in some other team."""
    elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": elsewhere.id}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "llm_provider_id" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()


@pytest.mark.django_db()
def test_a_reference_to_the_teams_own_resource_is_accepted(client, chatbot, team):
    """Guards the guard: the refusal above has to be about the team boundary, not about the
    reference check refusing every id it is shown."""
    material = SourceMaterialFactory.create(team=team)

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"source_material_id": material.id}},
        format="json",
    )

    assert response.status_code == 201, response.content


@pytest.mark.django_db()
def test_a_duplicate_node_name_persists_and_is_reported(client, chatbot, llm):
    """`name` is how one node reaches another's output, so a clash breaks the pipeline -- but it is
    structural, so it is reported rather than refused."""
    provider, model = llm
    params = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id, "name": "classifier"}

    first = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")
    second = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")

    assert (first.status_code, second.status_code) == (201, 201), second.content
    clashing = second.json()["pipeline_errors"]["node"]
    assert [error["name"] for error in clashing.values()] == ["All node names must be unique"] * 2


@pytest.mark.django_db()
def test_a_list_valued_reference_is_checked_per_entry(client, chatbot, team):
    """`collection_index_ids` holds a list, so the check has to look at every entry rather than at
    the list as a whole."""
    ours = CollectionFactory.create(team=team, is_index=True)
    theirs = CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True)

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"collection_index_ids": [ours.id, theirs.id]}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert str(theirs.id) in response.json()["params"]["collection_index_ids"]
    assert str(ours.id) not in response.json()["params"]["collection_index_ids"]


@pytest.mark.django_db()
def test_a_malformed_custom_action_reference_is_refused(client, chatbot):
    """`custom_actions` entries are the composite "{action_id}:{operation_id}" strings the server
    hands out, and `Node.update_from_params` splits them on the colon -- so a value that is not one
    would be a 500 rather than a rejected write if it reached the save."""
    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"custom_actions": ["not-a-reference"]}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "custom_actions" in response.json()["params"]
    assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()


# ---------------------------------------------------------------------------------------------
# One test per served node type, each sending every param that type declares. Written out with the
# whole body as a literal rather than parametrised over a table, so what was sent can be read at
# the place it is sent.
#
# Each asserts `body["node"]["params"] == <the params sent>`, an equality in both directions: the
# endpoint stored what it was given, and -- because a node is stored with a value for every param
# its type declares -- the payload named every param there is. So a param added to a type fails
# that type's test rather than quietly going untested.
# ---------------------------------------------------------------------------------------------

#: The types covered below. Guarded by `test_every_served_type_sends_a_full_payload`, which is what
#: fails when a new node type is served and nothing here sends it a full body.
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


def test_every_served_type_sends_a_full_payload():
    assert {node_type["type"] for node_type in get_node_types()} == FULL_PAYLOAD_TYPES


@pytest.mark.django_db()
def test_create_a_code_node_with_every_param(client, chatbot):
    payload = {
        "type": "CodeNode",
        "label": "Trim the answer",
        "params": {
            "name": "trim_answer",
            "code": "def main(input: str, **kwargs) -> str:\n    return input.strip()\n",
            "tag": "trimmed",
        },
    }

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Trim the answer"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_create_a_render_template_node_with_every_param(client, chatbot):
    payload = {
        "type": "RenderTemplate",
        "label": "Format the reply",
        "params": {
            "name": "format_reply",
            "template_string": "Hi {{ participant_data.name }} -- {{ input }}",
            "tag": "formatted",
        },
    }

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Format the reply"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_create_a_send_email_node_with_every_param(client, chatbot):
    """`recipient_list` is stored as sent, spacing included: the model only checks that the addresses
    parse, since the field doubles as a Jinja template rendered at run time."""
    payload = {
        "type": "SendEmail",
        "label": "Email the transcript",
        "params": {
            "name": "email_transcript",
            "recipient_list": "support@example.test, escalations@example.test",
            "subject": "Transcript for {{ participant_details.identifier }}",
            "body": "{{ input }}",
            "tag": "emailed",
        },
    }

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Email the transcript"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_create_an_extract_structured_data_node_with_every_param(client, chatbot, llm):
    provider, model = llm
    payload = {
        "type": "ExtractStructuredData",
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

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Pull out the order"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_create_an_extract_participant_data_node_with_every_param(client, chatbot, llm):
    """`ExtractStructuredData` plus `key_name`, which nests what it extracted under that key in the
    participant's data instead of merging it in at the top level."""
    provider, model = llm
    payload = {
        "type": "ExtractParticipantData",
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

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Remember the order"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


@pytest.mark.django_db()
def test_create_a_router_node_with_every_param(client, chatbot, llm):
    """`keywords` come back upper-cased -- the model does that on the way in, so the handles and the
    edges keyed off them are too. A router's prompt sees a narrower set of template variables than an
    LLM node's: `participant_data`, `temp_state`, `session_state`, and nothing resource-backed.
    """
    provider, model = llm
    payload = {
        "type": "RouterNode",
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

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Triage the request"
    assert body["node"]["params"] == {
        **payload["params"],
        "keywords": ["SCHEDULE", "RESCHEDULE", "CANCEL"],
    }
    assert body["node"]["output_handles"] == [
        {"handle": "output_0", "label": "SCHEDULE"},
        {"handle": "output_1", "label": "RESCHEDULE"},
        {"handle": "output_2", "label": "CANCEL"},
    ]
    assert body["pipeline_errors"]["node"] == {}


@pytest.mark.django_db()
def test_create_a_static_router_node_with_every_param(client, chatbot):
    """Routes on a key in stored data rather than by asking a model, so it takes no LLM params --
    `data_source` says which of the three data bags to read and `route_key` which key in it."""
    payload = {
        "type": "StaticRouterNode",
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

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Route on the plan"
    assert body["node"]["params"] == {**payload["params"], "keywords": ["FREE", "PRO"]}
    assert body["node"]["output_handles"] == [
        {"handle": "output_0", "label": "FREE"},
        {"handle": "output_1", "label": "PRO"},
    ]
    assert body["pipeline_errors"]["node"] == {}


@pytest.mark.django_db()
def test_create_an_llm_node_with_every_param(client, chatbot, llm, llm_node_resources):
    """The type with the most to say, and the only one whose params constrain each other: a resource
    param and its prompt variable each require the other, so `source_material_id` obliges the prompt
    to use `{source_material}`, `collection_id` obliges `{media}`, more than one index obliges
    `{collection_index_summaries}`, and a tool obliges whatever it needs.

    `built_in_tools` and `tool_config` are the LLM provider's own vocabulary, served by
    `/pipeline/options/` for a client to respect. The API does not hold a write to it: unlike a
    resource id, an unusable tool name is a run-time failure of that provider's.
    """
    provider, model = llm
    support_kb, billing_kb = llm_node_resources.collection_indexes
    payload = {
        "type": "LLMResponseWithPrompt",
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

    response = client.post(nodes_url(chatbot), payload, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["node"]["label"] == "Answer the question"
    assert body["node"]["params"] == payload["params"]
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= payload["params"].items()


def _place(pipeline, start: int, end: int) -> None:
    """Give the start and end nodes an x each: the factory leaves positions null, so a test about
    layout has to supply them."""
    pipeline.node_set.filter(type="StartNode").update(position_x=start, position_y=200)
    pipeline.node_set.filter(type="EndNode").update(position_x=end, position_y=200)


def _end_node(pipeline) -> Node:
    return Node.objects.get(pipeline=pipeline, type="EndNode")
