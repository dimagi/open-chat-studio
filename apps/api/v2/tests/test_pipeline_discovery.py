import pytest
from django.urls import reverse

from apps.api.v2.discovery import _clean_options, _documentation_url
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


def test_pipeline_nodes_reverse_is_versioned():
    assert reverse("api:v2:pipeline-nodes") == "/api/v2/pipeline/nodes/"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_node_types(auth_method, team):
    client = ApiTestClient(team.members.first(), team, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-nodes"))

    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()}
    assert by_type["LLMResponseWithPrompt"]["description"]
    assert by_type["LLMResponseWithPrompt"]["schema"]["properties"]


@pytest.mark.django_db()
def test_deprecated_node_types_are_excluded(team):
    client = ApiTestClient(team.members.first(), team)
    types = {entry["type"] for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert {"AssistantNode", "BooleanNode", "LLMResponse"}.isdisjoint(types)


@pytest.mark.django_db()
def test_only_addable_node_types_are_listed(team):
    """The endpoint answers "what can I build", so the answer is the list itself. Shipping the
    server-managed types with a `can_add: false` flag made that one boolean among six fields, which
    an agent scanning the list will sometimes miss."""
    client = ApiTestClient(team.members.first(), team)
    entries = client.get(reverse("api:v2:pipeline-nodes")).json()

    types = {entry["type"] for entry in entries}
    assert {"StartNode", "EndNode", "Passthrough"}.isdisjoint(types)
    assert "LLMResponseWithPrompt" in types
    assert not [entry for entry in entries if "can_add" in entry]


@pytest.mark.django_db()
def test_no_ui_key_survives_anywhere(team):
    """`ui:` is the builder's vocabulary. The API translates the two keys that carry meaning for an
    agent (`ui:optionsSource`, `ui:visibleWhen`) into `options_source`/`applies_when` and drops the
    rest, so nothing reaches the agent still labelled as a UI concern -- notably `ui:widget: "none"`,
    which sits on required fields like `llm_provider_model_id` and reads as "do not set this"."""
    client = ApiTestClient(team.members.first(), team)
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        assert not [key for key in entry["schema"] if key.startswith("ui:")], entry["type"]
        for name, prop in entry["schema"]["properties"].items():
            assert not [key for key in prop if key.startswith("ui:")], f"{entry['type']}.{name}"


@pytest.mark.django_db()
def test_declared_options_source_is_translated(team):
    """The builder's `ui:optionsSource` becomes `options_source` -- same join, agent vocabulary."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    source_material_id = by_type["LLMResponseWithPrompt"]["schema"]["properties"]["source_material_id"]
    assert source_material_id["options_source"] == "source_material"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "param", "expected_key"),
    [
        pytest.param("LLMResponseWithPrompt", "llm_provider_id", "llm_provider_id", id="llm-provider"),
        pytest.param("LLMResponseWithPrompt", "llm_provider_model_id", "llm_provider_model_id", id="llm-model"),
        pytest.param("LLMResponseWithPrompt", "tool_config", "built_in_tools_config", id="tool-config"),
        pytest.param("LLMResponseWithPrompt", "synthetic_voice_id", "synthetic_voice_id", id="synthetic-voice"),
    ],
)
def test_params_the_builder_never_linked_are_linked_here(team, node_type, param, expected_key):
    """These four params have no `ui:optionsSource` -- the builder hard-codes their widgets instead.
    The endpoint used to tell the agent to infer the link "from context", i.e. to guess. The link is
    synthesised here so `options_source` is a rule with no exceptions."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type[node_type]["schema"]["properties"][param]["options_source"] == expected_key


@pytest.mark.django_db()
def test_every_options_source_resolves_to_an_options_key(team_with_resources):
    """The join must be total: every `options_source` a node param declares has to name a key that
    `/pipeline/options/` actually returns, or the agent follows a dangling pointer."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    option_keys = set(client.get(reverse("api:v2:pipeline-options")).json())

    dangling = {
        f"{entry['type']}.{name}": prop["options_source"]
        for entry in client.get(reverse("api:v2:pipeline-nodes")).json()
        for name, prop in entry["schema"]["properties"].items()
        if "options_source" in prop and prop["options_source"] not in option_keys
    }
    assert not dangling


@pytest.mark.django_db()
def test_conditional_params_declare_when_they_apply(team):
    """`ui:visibleWhen` is real semantics wearing a UI name: the param is meaningless unless the
    condition holds. An agent needs it to avoid setting fields that will be ignored."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    token_limit = by_type["LLMResponseWithPrompt"]["schema"]["properties"]["user_max_token_limit"]
    assert token_limit["applies_when"] == {
        "field": "history_mode",
        "operator": "in",
        "value": ["summarize", "truncate_tokens"],
    }


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "expected"),
    [
        pytest.param(
            "RouterNode",
            {"kind": "per_keyword", "handle_pattern": "output_{index}"},
            id="router-has-one-output-per-keyword",
        ),
        pytest.param(
            "StaticRouterNode",
            {"kind": "per_keyword", "handle_pattern": "output_{index}"},
            id="static-router-has-one-output-per-keyword",
        ),
        pytest.param("LLMResponseWithPrompt", {"kind": "single", "handles": ["output"]}, id="plain-node"),
        pytest.param("CodeNode", {"kind": "single", "handles": ["output"]}, id="another-plain-node"),
    ],
)
def test_node_types_declare_their_output_topology(team, node_type, expected):
    """Discovery has to be enough to build a graph, not just to fill in params. Without this an
    agent cannot tell which types fan out or what to put in an edge's `source_handle`."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    outputs = by_type[node_type]["outputs"]
    assert {key: outputs[key] for key in expected} == expected
    assert outputs["description"].strip()


@pytest.mark.django_db()
def test_documented_node_types_expose_their_documentation_url(team):
    """The builder links human docs from the node's help button; an agent that can fetch a URL gets
    more from it than from any description we can inline. The stored link is site-relative, so it
    has to be absolutised -- a client has no `DOCUMENTATION_BASE_URL` to join it to."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type["LLMResponseWithPrompt"]["documentation_url"].startswith("http")


def test_a_node_type_without_a_doc_link_omits_the_key():
    """Every addable type happens to carry a link today, so the omission path needs a direct test:
    the key is absent rather than emitted as null."""
    assert _documentation_url({"title": "Whatever"}) is None


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("LLMResponseWithPrompt", id="llm-node"),
        pytest.param("RouterNode", id="router-node"),
        pytest.param("ExtractStructuredData", id="extract-node"),
    ],
)
def test_the_model_must_match_its_provider(team, node_type):
    """`default_values` quietly encodes one valid (provider, model) pair; the rule producing it was
    never stated. An agent pairing an OpenAI provider with an Anthropic model gets a 400 it cannot
    predict from the schema."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    model_id = by_type[node_type]["schema"]["properties"]["llm_provider_model_id"]
    assert model_id["must_match"] == {"field": "llm_provider_id", "on": "type"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "param",
    [
        pytest.param("built_in_tools", id="built-in-tools"),
        pytest.param("tool_config", id="tool-config"),
    ],
)
def test_provider_keyed_params_say_what_keys_them(team, param):
    """`built_in_tools` and `built_in_tools_config` are dicts keyed by provider type, not flat lists.
    Without this the agent has to guess which sub-list its chosen provider unlocks."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    prop = by_type["LLMResponseWithPrompt"]["schema"]["properties"][param]
    assert prop["options_keyed_by"] == {"field": "llm_provider_id", "on": "type"}


@pytest.mark.django_db()
def test_feature_flagged_params_are_marked(team):
    """`mcp_tools` is inert unless the team has `flag_mcp`. An agent that writes it anyway produces a
    node whose tools never load, with nothing in the response to explain why."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type["LLMResponseWithPrompt"]["schema"]["properties"]["mcp_tools"]["requires_feature_flag"] == "flag_mcp"


@pytest.mark.django_db()
def test_type_filter_returns_a_single_element_array(team):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": "RouterNode"})

    assert response.status_code == 200
    assert [entry["type"] for entry in response.json()] == ["RouterNode"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("Frobnicator", id="unknown-type"),
        pytest.param("BooleanNode", id="deprecated-type-is-not-discoverable"),
    ],
)
def test_type_filter_404s(team, node_type):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type})

    assert response.status_code == 404
    # Guards the shape `NodeTypeNotFoundSerializer` documents in the OpenAPI schema.
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_404_body_lists_the_types_the_agent_could_have_asked_for(team):
    """An error body is the agent's retry prompt. `detail` alone is a dead end; the valid names
    cost nothing to include and turn a failed call into a corrected one."""
    client = ApiTestClient(team.members.first(), team)
    body = client.get(reverse("api:v2:pipeline-nodes"), {"type": "Frobnicator"}).json()

    listed = {entry["type"] for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}
    assert set(body["valid_types"]) == listed


@pytest.mark.django_db()
def test_deprecated_type_is_reported_as_deprecated_not_unknown(team):
    """ "Unknown node type: BooleanNode" sends the agent looking for a typo. The type exists and has a
    replacement -- saying so is the difference between a retry and a dead end."""
    client = ApiTestClient(team.members.first(), team)
    detail = client.get(reverse("api:v2:pipeline-nodes"), {"type": "BooleanNode"}).json()["detail"]

    assert "deprecated" in detail.lower()
    assert "Router" in detail


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("StartNode", id="start"),
        pytest.param("EndNode", id="end"),
        pytest.param("Passthrough", id="passthrough"),
    ],
)
def test_structural_type_is_reported_as_server_managed_not_unknown(team, node_type):
    """These are gone from the list, but `/inspect/` still reports them as the `type` of real nodes.
    An agent that looks one up must be told it is server-managed, not that it made the name up."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type})

    assert response.status_code == 404
    assert "managed by the server" in response.json()["detail"]
    assert "Unknown" not in response.json()["detail"]


@pytest.mark.django_db()
def test_node_list_is_revalidatable(team):
    """The payload is static per deploy and already memoised, so an agent that caches it should be
    able to confirm its copy is current without paying for the body again."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"))
    etag = response.headers["ETag"]

    revalidated = client.get(reverse("api:v2:pipeline-nodes"), HTTP_IF_NONE_MATCH=etag)

    assert revalidated.status_code == 304
    assert not revalidated.content


@pytest.mark.django_db()
def test_etag_distinguishes_the_filtered_response(team):
    """`?type=` returns a different body, so it must not validate against the full list's ETag."""
    client = ApiTestClient(team.members.first(), team)
    full = client.get(reverse("api:v2:pipeline-nodes")).headers["ETag"]

    filtered = client.get(reverse("api:v2:pipeline-nodes"), {"type": "RouterNode"})

    assert filtered.headers["ETag"] != full


@pytest.mark.django_db()
def test_unauthenticated_request_is_rejected(team, client):
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 401


@pytest.mark.django_db()
def test_read_only_api_key_may_read(team):
    """The inspect key is read-only; discovery is a GET, so it must work."""
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 200


@pytest.fixture()
def team_with_resources(db):
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team, type="openai", name="Prod OpenAI")
    LlmProviderModelFactory.create(team=team, type="openai")
    VoiceProviderFactory.create(team=team, name="Prod Polly")
    SourceMaterialFactory.create(team=team, topic="Returns policy")
    # CollectionFactory's SubFactories would otherwise auto-create an LlmProvider and an
    # EmbeddingProviderModel on this team, polluting the llm_provider_id assertions below.
    # A media collection (is_index=False) has neither in production anyway.
    CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    return team


def test_pipeline_options_reverse_is_versioned():
    assert reverse("api:v2:pipeline-options") == "/api/v2/pipeline/options/"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_options_lists_team_resources(auth_method, team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-options"))

    assert response.status_code == 200
    options = response.json()
    assert [option["label"] for option in options["llm_provider_id"]] == ["Prod OpenAI"]
    assert [option["label"] for option in options["source_material"]] == ["Returns policy"]
    assert [option["label"] for option in options["collection"]] == ["Policy docs"]


@pytest.mark.django_db()
def test_options_are_team_scoped(team_with_resources):
    """Another team's resources must not leak into this team's options."""
    other_team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=other_team, name="Their OpenAI")
    VoiceProviderFactory.create(team=other_team, name="Their Polly")
    SourceMaterialFactory.create(team=other_team, topic="Their policy")
    # Same SubFactory pollution as in `team_with_resources` above -- pin to None or an unwanted
    # LlmProvider on `other_team` would land in the leak-check labels below.
    CollectionFactory.create(
        team=other_team, name="Their docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    labels = {
        label
        for key in ("llm_provider_id", "voice_provider_id", "source_material", "collection")
        for label in (option["label"] for option in options[key])
    }
    assert not {"Their OpenAI", "Their Polly", "Their policy", "Their docs"} & labels


@pytest.mark.django_db()
def test_options_carry_no_placeholder_entries(team_with_resources):
    """The builder emits `{"value": "", "label": "Select a topic"}`; an empty id is useless here."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    for key, value in options.items():
        if isinstance(value, list):
            assert all(option.get("value") != "" for option in value if isinstance(option, dict)), key


@pytest.mark.django_db()
def test_options_carry_no_edit_urls(team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    assert "edit_url" not in client.get(reverse("api:v2:pipeline-options")).content.decode()


def test_clean_options_recurses_into_nested_dicts():
    """`built_in_tools` is a dict of lists keyed by provider type -- a `_clean_options` that only
    special-cased the top-level-list case would silently skip it, leaving placeholder entries and
    `edit_url` buried at depth >= 2 untouched. `edit_url` is exercised at depth 3 via
    `built_in_tools_config` for the same reason.

    Real `built_in_tools_config` entries (`BuiltInTools.get_tool_configs_by_provider`) are
    `{name, type, label, helpText}` descriptors with no `value` key at all, so the placeholder strip
    (which only matches `option.get("value") == ""`) can never touch them -- they must survive
    `_clean_options` unchanged. A fixture that gave them a `value` key, as an earlier version of this
    test did, would lock in "silently drop a config descriptor" as intended behaviour, which is not
    the actual contract."""
    nested = {
        "built_in_tools": {
            "openai": [
                {"value": "web-search", "label": "Web Search", "edit_url": "/tools/web-search"},
                {"value": "", "label": "Select a tool"},
            ],
        },
        "built_in_tools_config": {
            "anthropic": {
                "web-search": [
                    {
                        "name": "allowed_domains",
                        "type": "expandable_text",
                        "label": "Allowed Domains",
                        "helpText": "Only search these domains. Separate entries with newlines.",
                        "edit_url": "/tools/anthropic/web-search",
                    },
                    {
                        "name": "blocked_domains",
                        "type": "expandable_text",
                        "label": "Blocked Domains",
                        "helpText": "Exclude these domains from search. Separate entries with newlines.",
                    },
                ],
            },
        },
    }

    cleaned = _clean_options(nested)

    assert cleaned["built_in_tools"]["openai"] == [{"value": "web-search", "label": "Web Search"}]
    assert cleaned["built_in_tools_config"]["anthropic"]["web-search"] == [
        {
            "name": "allowed_domains",
            "type": "expandable_text",
            "label": "Allowed Domains",
            "helpText": "Only search these domains. Separate entries with newlines.",
        },
        {
            "name": "blocked_domains",
            "type": "expandable_text",
            "label": "Blocked Domains",
            "helpText": "Exclude these domains from search. Separate entries with newlines.",
        },
    ]


@pytest.mark.django_db()
def test_options_include_voice_providers_with_type(team_with_resources):
    """`type` is the join key for the voice-pairing rule on the chatbot settings endpoint."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    voice_providers = client.get(reverse("api:v2:pipeline-options")).json()["voice_provider_id"]

    assert [option["label"] for option in voice_providers] == ["Prod Polly"]
    assert voice_providers[0]["type"] == "aws"


@pytest.mark.django_db()
def test_options_synthetic_voices_are_filtered_by_team_voice_provider_type(team_with_resources):
    """`SyntheticVoice.service` ("AWS") and `VoiceProviderType` ("aws") differ only in case -- a naive
    `service__in` match against the team's provider types would return nothing, the same trap the
    chatbot builder avoids with `service__iexact`. `team_with_resources` has an AWS voice provider,
    so AWS voices must be listed; Azure/OpenAI voices -- reachable to every team via `get_for_team`
    since none of the three are team-scoped services -- must not be, absent a matching provider."""
    SyntheticVoiceFactory.create(name="Aria", service="AWS")
    SyntheticVoiceFactory.create(name="Elan", service="Azure")
    SyntheticVoiceFactory.create(name="Coral", service="OpenAI")

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    voices = client.get(reverse("api:v2:pipeline-options")).json()["synthetic_voice_id"]

    services = {voice["type"] for voice in voices}
    assert "aws" in services
    assert not {"azure", "openai"} & services


@pytest.mark.django_db()
def test_options_synthetic_voices_are_empty_without_a_voice_provider(team):
    """A team with no VoiceProvider can reach no synthetic voice, however many exist generally --
    this is the 564-unreachable-voices bug the filtering in the view fixes."""
    SyntheticVoiceFactory.create(service="AWS")
    SyntheticVoiceFactory.create(service="Azure")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["synthetic_voice_id"] == []


@pytest.mark.django_db()
def test_options_include_a_valid_starting_provider_pair(team_with_resources):
    """Named `default_llm_provider`, not `default_values`: it covers two params, not every param's
    default -- those live in each node schema's `default`."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    defaults = client.get(reverse("api:v2:pipeline-options")).json()["default_llm_provider"]

    assert defaults["llm_provider_id"] is not None
    assert defaults["llm_provider_model_id"] is not None


@pytest.mark.django_db()
def test_option_keys_are_all_snake_case(team_with_resources):
    """`LlmProviderId` next to `source_material` makes the agent guess a convention on every lookup.
    The builder keeps the mixed-case `OptionsSource` names; only the API surface is normalised."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert not [key for key in options if key != key.lower()]


@pytest.mark.django_db()
def test_options_can_be_scoped_to_one_node_type(team_with_resources):
    """An agent configures one node at a time. Unfiltered, it pays for every other node type's
    options -- mostly synthetic voices and built-in tool configs it will never reference."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": "RenderTemplate"}).json()

    assert set(scoped) == {"jinja_node"}


@pytest.mark.django_db()
def test_scoped_options_keep_the_provider_defaults_for_llm_nodes(team_with_resources):
    """A scoped response still has to be enough to build the node on its own."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": "LLMResponseWithPrompt"}).json()

    assert "default_llm_provider" in scoped
    assert "llm_provider_id" in scoped
    assert "jinja_node" not in scoped


@pytest.mark.django_db()
def test_scoped_options_reject_an_unknown_node_type(team_with_resources):
    """Same failure the agent already knows from `/pipeline/nodes/?type=`, same recoverable body."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-options"), {"node_type": "Frobnicator"})

    assert response.status_code == 404
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_a_node_type_that_references_nothing_scopes_to_an_empty_object(team_with_resources):
    """ "No options" and "no such type" are different answers. Collapsing them would 404 on a type
    the agent just read out of /pipeline/nodes/, which reads as the endpoints disagreeing.
    `CodeNode` is addable and every param is free text."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-options"), {"node_type": "CodeNode"})

    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db()
def test_scoping_to_a_structural_type_404s_like_the_node_list(team_with_resources):
    """The two endpoints must agree on which types exist to be configured."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-options"), {"node_type": "StartNode"})

    assert response.status_code == 404
    assert "managed by the server" in response.json()["detail"]


@pytest.mark.django_db()
def test_options_never_expose_provider_config(team_with_resources):
    """Providers are reference-only -- their `config` holds credentials."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    body = client.get(reverse("api:v2:pipeline-options")).content.decode()

    assert "openai_api_key" not in body
    assert "aws_secret_access_key" not in body


@pytest.mark.django_db()
def test_options_unauthenticated_request_is_rejected(team_with_resources, client):
    assert client.get(reverse("api:v2:pipeline-options")).status_code == 401


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "options_key",
    [
        pytest.param("text_editor_autocomplete_vars_llm_node", id="llm-node-vars"),
        pytest.param("text_editor_autocomplete_vars_router_node", id="router-node-vars"),
        pytest.param("jinja_node", id="jinja-node-vars"),
    ],
)
def test_prompt_var_options_carry_a_description_not_a_value(team_with_resources, options_key):
    """The builder emits {"label": v, "value": v} -- the two are always identical, so the value
    tells an agent nothing. It gets a description of what the variable holds instead."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    entries = client.get(reverse("api:v2:pipeline-options")).json()[options_key]

    assert entries
    for entry in entries:
        assert sorted(entry) == ["description", "label"], entry
        assert entry["description"].strip()
        assert entry["description"] != entry["label"]


@pytest.mark.django_db()
def test_prompt_var_descriptions_are_the_real_ones(team_with_resources):
    """Pins one description end-to-end so a refactor can't quietly serve placeholder text."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    by_label = {
        entry["label"]: entry["description"]
        for entry in client.get(reverse("api:v2:pipeline-options")).json()["jinja_node"]
    }

    assert by_label["input"] == PROMPT_VAR_DESCRIPTIONS["input"]
    assert "session" in by_label["session_state"]
    assert "run" in by_label["temp_state"]


@pytest.mark.django_db()
def test_resource_option_lists_keep_their_value(team_with_resources):
    """Only the prompt-variable keys are reshaped -- resource lists still carry the id to write."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["source_material"][0]["value"]
    assert "description" not in options["source_material"][0]
