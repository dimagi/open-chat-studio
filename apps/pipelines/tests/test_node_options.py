"""The option values each node param accepts, and which of a team's resources reach them.

Covers `apps.pipelines.nodes.node_metadata` and the two consumers it feeds: the pipeline builder view
and the v2 `/pipeline/options/` endpoint. The endpoint's own contract -- auth, scoping by node type,
response shaping -- lives in `apps/api/v2/tests/test_pipeline_discovery.py`.
"""

import pytest
from django.urls import reverse

from apps.pipelines.nodes.node_metadata import get_node_default_values, get_node_parameter_values
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def team_with_resources(db):
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team, type="openai", name="Prod OpenAI")
    LlmProviderModelFactory.create(team=team, type="openai")
    VoiceProviderFactory.create(team=team, name="Prod Polly")
    SourceMaterialFactory.create(team=team, topic="Returns policy")
    # Pinned to None so CollectionFactory's SubFactories don't add an LlmProvider to this team.
    CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    return team


@pytest.mark.django_db()
def test_get_node_parameter_values_is_team_scoped():
    """The provider list is passed in, but the resource lists are queried here, so the team argument
    is what keeps another team's rows out."""
    team = TeamWithUsersFactory.create()
    other_team = TeamWithUsersFactory.create()
    mine = LlmProviderFactory.create(team=team)
    theirs = LlmProviderFactory.create(team=other_team)

    values = get_node_parameter_values(
        team=team,
        llm_providers=list(LlmProvider.objects.filter(team=team).values("id", "name", "type")),
        llm_provider_models=LlmProviderModel.objects.for_team(team),
        synthetic_voices=[],
    )

    provider_ids = {option["value"] for option in values["llm_provider_id"]}
    assert mine.id in provider_ids
    assert theirs.id not in provider_ids


@pytest.mark.django_db()
def test_every_option_key_is_snake_case():
    """The builder and the v2 discovery API both read this payload verbatim."""
    team = TeamWithUsersFactory.create()

    values = get_node_parameter_values(
        team=team,
        llm_providers=list(LlmProvider.objects.filter(team=team).values("id", "name", "type")),
        llm_provider_models=LlmProviderModel.objects.for_team(team),
        synthetic_voices=[],
    )

    assert [key for key in values if key != key.lower()] == []


@pytest.mark.django_db()
def test_a_zero_max_token_limit_is_served_rather_than_dropped(team):
    """0 is a real limit -- it turns history compression off -- so a caller reading the option list
    has to be able to tell it apart from a model with no limit recorded."""
    LlmProviderFactory.create(team=team, type="openai")
    LlmProviderModelFactory.create(team=team, type="openai", max_token_limit=0)

    values = get_node_parameter_values(
        team=team,
        llm_providers=list(LlmProvider.objects.filter(team=team).values("id", "name", "type")),
        llm_provider_models=LlmProviderModel.objects.filter(team=team),
        synthetic_voices=[],
    )

    assert [option["max_token_limit"] for option in values["llm_provider_model_id"]] == [0]


@pytest.mark.django_db()
def test_get_node_default_values_pairs_a_provider_with_a_type_matching_model():
    """A default provider paired with a model of another type is a pair no node would accept, so the
    search walks the providers until one has a model of its own type."""
    team = TeamWithUsersFactory.create()
    provider = LlmProviderFactory.create(team=team, type="openai")
    # Own the model row: a `django_db(transaction=True)` test elsewhere flushes the global seed rows.
    model = LlmProviderModelFactory.create(team=team, type="openai")

    defaults = get_node_default_values(
        list(LlmProvider.objects.filter(team=team).values("id", "name", "type")),
        LlmProviderModel.objects.filter(team=team),
    )

    assert defaults["llm_provider_id"] == provider.id
    assert defaults["llm_provider_model_id"] == model.id


@pytest.mark.django_db()
def test_pipeline_builder_context_still_populated(client):
    """The builder view still gets its three context keys from the extracted helpers."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    LlmProviderFactory.create(team=team)
    pipeline = PipelineFactory.create(team=team)
    client.force_login(user)

    response = client.get(reverse("pipelines:edit", kwargs={"team_slug": team.slug, "pk": pipeline.id}))

    assert response.status_code == 200
    assert response.context["node_schemas"]
    assert response.context["parameter_values"]["llm_provider_id"]
    assert "llm_provider_id" in response.context["default_values"]


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_options_lists_team_resources(auth_method, team_with_resources):
    """Each key holds the team's stored rows for the node param of the same name."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-options"))

    assert response.status_code == 200
    options = response.json()
    assert [option["label"] for option in options["llm_provider_id"]] == ["Prod OpenAI"]
    assert [option["label"] for option in options["source_material"]] == ["Returns policy"]
    assert [option["label"] for option in options["collection"]] == ["Policy docs"]


@pytest.mark.django_db()
def test_options_are_team_scoped(team_with_resources):
    """Nothing another team owns is offered, in any of the lists."""
    other_team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=other_team, name="Their OpenAI")
    VoiceProviderFactory.create(team=other_team, name="Their Polly")
    SourceMaterialFactory.create(team=other_team, topic="Their policy")
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
def test_options_include_voice_providers_with_type(team_with_resources):
    """`type` is the join key for the voice-pairing rule on the chatbot settings endpoint."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    voice_providers = client.get(reverse("api:v2:pipeline-options")).json()["voice_provider_id"]

    assert [option["label"] for option in voice_providers] == ["Prod Polly"]
    assert voice_providers[0]["type"] == "aws"


@pytest.mark.django_db()
def test_options_synthetic_voices_are_filtered_by_team_voice_provider_type(team_with_resources):
    """`SyntheticVoice.service` ("AWS") and `VoiceProviderType` ("aws") differ only in case, so a
    naive `service__in` match would return nothing. Azure and OpenAI voices reach every team via
    `get_for_team`, so only the provider filter excludes them."""
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
    """The voice rows exist for every team, but with no provider to speak them none is selectable."""
    SyntheticVoiceFactory.create(service="AWS")
    SyntheticVoiceFactory.create(service="Azure")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["synthetic_voice_id"] == []


@pytest.mark.django_db()
def test_options_llm_models_are_filtered_by_team_provider_type(team_with_resources):
    """Global models (`team=None`) reach every team, so without the provider filter a team holding
    only an OpenAI key is offered the ~60 models it has no provider to call. Every configured type
    survives, not just the first -- a team can hold providers for several."""
    # team_with_resources already has an openai provider, so lets add another one
    LlmProviderFactory.create(team=team_with_resources, type="anthropic", name="Prod Anthropic")
    LlmProviderModelFactory.create(team=None, type="anthropic", name="claude-sonnet-5")
    LlmProviderModelFactory.create(team=None, type="google", name="gemini-3-pro")
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-5.1")

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    models = client.get(reverse("api:v2:pipeline-options")).json()["llm_provider_model_id"]

    assert {model["type"] for model in models} == {"openai", "anthropic"}


@pytest.mark.django_db()
def test_options_omit_deprecated_llm_models(team_with_resources):
    """Absent rather than flagged, the same as a deprecated node type. The builder still shows these
    so an existing node keeps rendering; this list is only what a client may build with."""
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-3.5-turbo", deprecated=True)

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    models = client.get(reverse("api:v2:pipeline-options")).json()["llm_provider_model_id"]

    assert models
    assert not [model for model in models if "gpt-3.5-turbo" in model["label"]]


@pytest.mark.django_db()
def test_options_tool_config_is_scoped_to_the_teams_provider_types(team_with_resources):
    """`tool_config` is a hardcoded dict covering every provider type, unlike `built_in_tools`, which
    is built from the team's providers. Both are keyed by `llm_provider_id.type`, so both are scoped.
    Anthropic is the only type carrying configs, so it is what the two cases are told apart by."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    openai_only = client.get(reverse("api:v2:pipeline-options")).json()

    assert set(openai_only["built_in_tools"]) == {"openai"}
    assert "anthropic" not in openai_only["tool_config"]

    LlmProviderFactory.create(team=team_with_resources, type="anthropic", name="Prod Anthropic")
    with_anthropic = client.get(reverse("api:v2:pipeline-options")).json()

    assert "anthropic" in with_anthropic["tool_config"]


@pytest.mark.django_db()
def test_options_llm_models_are_empty_without_an_llm_provider(team):
    """A model with no provider behind it cannot be called, so neither the list nor the default pair
    offers one."""
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-5.1")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["llm_provider_id"] == []
    assert options["llm_provider_model_id"] == []
    assert options["default_llm_provider"] == {"llm_provider_id": None, "llm_provider_model_id": None}


@pytest.mark.django_db()
def test_options_include_a_valid_starting_provider_pair(team_with_resources):
    """`default_llm_provider` is a provider/model pair a client can write without working through the
    `must_match` rule itself."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    defaults = client.get(reverse("api:v2:pipeline-options")).json()["default_llm_provider"]

    assert defaults["llm_provider_id"] is not None
    assert defaults["llm_provider_model_id"] is not None


@pytest.mark.django_db()
def test_options_never_expose_provider_config(team_with_resources):
    """Providers are reference-only -- their `config` holds credentials."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    body = client.get(reverse("api:v2:pipeline-options")).content.decode()

    assert "openai_api_key" not in body
    assert "aws_secret_access_key" not in body
