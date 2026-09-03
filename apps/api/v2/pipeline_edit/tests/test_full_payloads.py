"""Every param of every served node type, written and read back (#4140).

One class per type, holding the payload once and sending it both ways: POST it as a whole body, and
PATCH it onto a node created from its type alone. The two verbs reach the same stored state by
different routes -- POST materializes the type's defaults and overwrites them in one step, PATCH
merges into defaults already on the row -- so a param that only one of them handles fails here.

Each class asserts ``body["node"]["params"] == <the params sent>``, an equality in both directions:
the endpoint stored what it was given, and -- because a node is stored with a value for every param
its type declares -- the payload named every param there is. So a param added to a type fails that
type's class rather than quietly going untested.

The payloads are literals rather than a parametrised table, so what was sent can be read at the
place it is sent.
"""

import pytest

from apps.api.v2.discovery.node_types import get_node_types

from .conftest import add_bare_node, node_url, nodes_url, stored_node_params

SERVED_TYPES = [node_type["type"] for node_type in get_node_types()]

#: The types covered below. Guarded by `test_every_served_type_sends_a_full_payload`, which is what
#: fails when a new node type is served and no class here sends it a full body.
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
@pytest.mark.parametrize("node_type", SERVED_TYPES, ids=SERVED_TYPES)
def test_a_write_response_is_a_valid_request_body(client, chatbot, node_type):
    """``WrittenNodeSerializer`` promises the shape you can PATCH again, and an agent's ordinary loop
    is read-modify-write. So for every served type: create it, send its own reported params back, and
    expect that to be accepted rather than argued with.
    """
    created = client.post(nodes_url(chatbot), {"type": node_type}, format="json")
    assert created.status_code == 201, created.content
    node = created.json()["node"]

    echoed = client.patch(node_url(chatbot, node["node_id"]), {"params": node["params"]}, format="json")

    assert echoed.status_code == 200, echoed.content


def create_in_full(client, chatbot, node_type: str, label: str, params: dict) -> dict:
    """POST the whole payload, and return the response body once it has been accepted."""
    response = client.post(nodes_url(chatbot), {"type": node_type, "label": label, "params": params}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def patch_in_full(client, chatbot, node_type: str, label: str, params: dict) -> dict:
    """PATCH the whole payload onto a node holding nothing but its type's defaults."""
    node_id = add_bare_node(client, chatbot, node_type)
    response = client.patch(node_url(chatbot, node_id), {"label": label, "params": params}, format="json")
    assert response.status_code == 200, response.content
    return response.json()


def assert_written(body: dict, chatbot, label: str, expected: dict) -> None:
    """The response reports the label and params asked for, reports no node error, and the row
    behind it holds them too."""
    assert body["node"]["label"] == label
    assert body["node"]["params"] == expected
    assert body["pipeline_errors"]["node"] == {}
    assert stored_node_params(chatbot, body["node"]["node_id"]).items() >= expected.items()


class FullPayload:
    """A type's whole payload, sent by each verb in turn.

    Subclasses name the type, its label and its params, and override ``expected`` where the model
    normalises a value on the way in. A subclass with more to say about the response -- a router's
    regenerated handles -- overrides ``assert_extras``.
    """

    node_type: str
    label: str

    @pytest.fixture()
    def params(self) -> dict:
        raise NotImplementedError

    def expected(self, params: dict) -> dict:
        """The params the response should report, given the ones sent."""
        return params

    def assert_extras(self, body: dict) -> None:
        """Anything true of the response beyond the label and params."""

    def test_create(self, client, chatbot, params):
        body = create_in_full(client, chatbot, self.node_type, self.label, params)

        assert_written(body, chatbot, self.label, self.expected(params))
        self.assert_extras(body)

    def test_patch(self, client, chatbot, params):
        body = patch_in_full(client, chatbot, self.node_type, self.label, params)

        assert_written(body, chatbot, self.label, self.expected(params))
        self.assert_extras(body)


@pytest.mark.django_db()
class TestCodeNode(FullPayload):
    node_type = "CodeNode"
    label = "Trim the answer"

    @pytest.fixture()
    def params(self):
        return {
            "name": "trim_answer",
            "code": "def main(input: str, **kwargs) -> str:\n    return input.strip()\n",
            "tag": "trimmed",
        }


@pytest.mark.django_db()
class TestRenderTemplate(FullPayload):
    node_type = "RenderTemplate"
    label = "Format the reply"

    @pytest.fixture()
    def params(self):
        return {
            "name": "format_reply",
            "template_string": "Hi {{ participant_data.name }} -- {{ input }}",
            "tag": "formatted",
        }


@pytest.mark.django_db()
class TestSendEmail(FullPayload):
    """`recipient_list` is stored as sent, spacing included: the model only checks that the addresses
    parse, since the field doubles as a Jinja template rendered at run time."""

    node_type = "SendEmail"
    label = "Email the transcript"

    @pytest.fixture()
    def params(self):
        return {
            "name": "email_transcript",
            "recipient_list": "support@example.test, escalations@example.test",
            "subject": "Transcript for {{ participant_details.identifier }}",
            "body": "{{ input }}",
            "tag": "emailed",
        }


@pytest.mark.django_db()
class TestExtractStructuredData(FullPayload):
    node_type = "ExtractStructuredData"
    label = "Pull out the order"

    @pytest.fixture()
    def params(self, llm):
        provider, model = llm
        return {
            "name": "extract_order",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.2},
            "data_schema": '{"order_number": "the order the participant is asking about"}',
            "tag": "extracted",
        }


@pytest.mark.django_db()
class TestExtractParticipantData(FullPayload):
    """`ExtractStructuredData` plus `key_name`, which nests what it extracted under that key in the
    participant's data instead of merging it in at the top level."""

    node_type = "ExtractParticipantData"
    label = "Remember the order"

    @pytest.fixture()
    def params(self, llm):
        provider, model = llm
        return {
            "name": "remember_order",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "llm_model_parameters": {"temperature": 0.2},
            "data_schema": '{"order_number": "the order the participant is asking about"}',
            "key_name": "orders",
            "tag": "remembered",
        }


@pytest.mark.django_db()
class TestRouterNode(FullPayload):
    """`keywords` come back upper-cased -- the model does that on the way in, so the handles and the
    edges keyed off them are too. A router's prompt sees a narrower set of template variables than an
    LLM node's: `participant_data`, `temp_state`, `session_state`, and nothing resource-backed.
    """

    node_type = "RouterNode"
    label = "Triage the request"

    @pytest.fixture()
    def params(self, llm):
        provider, model = llm
        return {
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
        }

    def expected(self, params):
        return {**params, "keywords": ["SCHEDULE", "RESCHEDULE", "CANCEL"]}

    def assert_extras(self, body):
        assert body["node"]["output_handles"] == [
            {"handle": "output_0", "label": "SCHEDULE"},
            {"handle": "output_1", "label": "RESCHEDULE"},
            {"handle": "output_2", "label": "CANCEL"},
        ]


@pytest.mark.django_db()
class TestStaticRouterNode(FullPayload):
    """Routes on a key in stored data rather than by asking a model, so it takes no LLM params --
    `data_source` says which of the three data bags to read and `route_key` which key in it."""

    node_type = "StaticRouterNode"
    label = "Route on the plan"

    @pytest.fixture()
    def params(self):
        return {
            "name": "route_on_plan",
            "keywords": ["free", "pro"],
            "default_keyword_index": 0,
            "tag_output_message": True,
            "data_source": "session_state",
            "route_key": "plan",
        }

    def expected(self, params):
        return {**params, "keywords": ["FREE", "PRO"]}

    def assert_extras(self, body):
        assert body["node"]["output_handles"] == [
            {"handle": "output_0", "label": "FREE"},
            {"handle": "output_1", "label": "PRO"},
        ]


@pytest.mark.django_db()
class TestLLMResponseWithPrompt(FullPayload):
    """The type with the most to say, and the only one whose params constrain each other: a resource
    param and its prompt variable each require the other, so `source_material_id` obliges the prompt
    to use `{source_material}`, `collection_id` obliges `{media}`, more than one index obliges
    `{collection_index_summaries}`, and a tool obliges whatever it needs.

    All in one write, which is the only way this node can reach that state: sending the resource and
    the prompt that names it in separate calls would have each refused on its own.

    `built_in_tools` and `tool_config` are the LLM provider's own vocabulary, served by
    `/pipeline/options/` for a client to respect. The API does not hold a write to it: unlike a
    resource id, an unusable tool name is a run-time failure of that provider's.
    """

    node_type = "LLMResponseWithPrompt"
    label = "Answer the question"

    @pytest.fixture()
    def params(self, llm, llm_node_resources):
        provider, model = llm
        support_kb, billing_kb = llm_node_resources.collection_indexes
        return {
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
        }
