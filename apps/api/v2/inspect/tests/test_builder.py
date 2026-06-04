import pytest

from apps.api.v2.inspect.builder import InspectVersionError, build_inspect_context, resolve_inspect_version
from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.channels.models import ExperimentChannel
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


def _payload(experiment) -> dict:
    return ChatbotInspectSerializer(build_inspect_context(experiment)).data


@pytest.fixture()
def chatbot_with_llm_node(db):
    team = TeamFactory.create()
    pipeline = PipelineFactory.create(team=team)
    provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai")
    model = LlmProviderModelFactory.create(team=team, name="gpt-4o", max_token_limit=128000)
    NodeFactory.create(
        pipeline=pipeline,
        type="LLMResponseWithPrompt",
        label="Classify intent",
        params={"llm_provider_id": provider.id, "llm_provider_model_id": model.id, "prompt": "You are helpful"},
    )
    # The team-global web/api channels are read (not created) by inspect; seed them explicitly.
    ExperimentChannel.objects.get_team_web_channel(team)
    ExperimentChannel.objects.get_team_api_channel(team)
    return ExperimentFactory.create(team=team, pipeline=pipeline)


@pytest.mark.django_db()
def test_payload_top_level_shape(chatbot_with_llm_node):
    experiment = chatbot_with_llm_node
    payload = _payload(experiment)

    assert payload["id"] == str(experiment.public_id)
    assert payload["name"] == experiment.name
    assert payload["is_unreleased"] is True
    assert payload["team_slug"] == experiment.team.slug
    # settings is a flat block of non-secret experiment fields
    assert "seed_message" in payload["settings"]
    assert payload["settings"]["participant_allowlist"] == []
    # voice is flattened from the experiment's voice_provider + synthetic_voice
    assert payload["voice"]["provider_name"] is not None
    assert "neural" in payload["voice"]
    # nothing else configured -> null / empty members
    assert payload["trace_provider"] is None
    # the team-global web + API channels are always present
    assert [(c["platform"], c["name"]) for c in payload["channels"]] == [
        ("web", f"{experiment.team.slug}-web-channel"),
        ("api", f"{experiment.team.slug}-api-channel"),
    ]
    assert payload["events"] == {"static_triggers": [], "timeout_triggers": []}


@pytest.mark.django_db()
def test_pipeline_node_embeds_flattened_llm(chatbot_with_llm_node):
    payload = _payload(chatbot_with_llm_node)
    pipeline = payload["pipeline"]
    assert pipeline["id"] == chatbot_with_llm_node.pipeline_id
    assert set(pipeline.keys()) == {"id", "name", "version_number", "graph", "nodes"}

    llm_node = next(n for n in pipeline["nodes"] if n["type"] == "LLMResponseWithPrompt")
    assert llm_node["label"] == "Classify intent"
    assert llm_node["params"] == {"prompt": "You are helpful"}
    assert llm_node["llm"]["provider_name"] == "Prod OpenAI"
    assert llm_node["llm"]["type"] == "openai"
    assert llm_node["llm"]["model"] == "gpt-4o"
    assert llm_node["llm"]["max_token_limit"] == 128000
    assert llm_node["llm"]["deprecated"] is False


@pytest.mark.django_db()
def test_pipeline_nodes_render_start_first_end_last(chatbot_with_llm_node):
    # The LLM node was created after the default start/end nodes, so creation order alone would
    # put it last — the renderer must still pin StartNode first and EndNode last.
    payload = _payload(chatbot_with_llm_node)
    node_types = [n["type"] for n in payload["pipeline"]["nodes"]]
    assert node_types == ["StartNode", "LLMResponseWithPrompt", "EndNode"]


@pytest.mark.django_db()
def test_channels_come_from_working_version():
    """Channels are only ever linked to the working version, so every inspected version
    must surface the working version's channels."""
    experiment = ExperimentFactory.create()
    ExperimentChannelFactory.create(experiment=experiment, name="working-telegram", platform="telegram")
    # The team-global web/api channels are read (not created) by inspect; seed them explicitly.
    ExperimentChannel.objects.get_team_web_channel(experiment.team)
    ExperimentChannel.objects.get_team_api_channel(experiment.team)
    version = experiment.create_new_version()

    payload = _payload(version)
    assert [c["name"] for c in payload["channels"]] == [
        "working-telegram",
        f"{experiment.team.slug}-web-channel",
        f"{experiment.team.slug}-api-channel",
    ]


@pytest.mark.django_db()
def test_resolve_version_working_is_default():
    experiment = ExperimentFactory.create()
    assert resolve_inspect_version(experiment, None) is experiment


@pytest.mark.django_db()
def test_resolve_unknown_version_raises():
    experiment = ExperimentFactory.create()
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(experiment, "999")
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(experiment, "not-a-number")
    with pytest.raises(InspectVersionError):
        # no published version exists yet
        resolve_inspect_version(experiment, "default")


@pytest.mark.django_db()
def test_voice_is_null_when_not_configured():
    experiment = ExperimentFactory.create(voice_provider=None, synthetic_voice=None)
    assert _payload(experiment)["voice"] is None


@pytest.mark.django_db()
def test_pipeline_is_null_when_not_configured():
    experiment = ExperimentFactory.create(pipeline=None)
    assert _payload(experiment)["pipeline"] is None
