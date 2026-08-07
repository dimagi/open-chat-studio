import pytest
from django.urls import reverse

from apps.pipelines.node_options import (
    get_node_default_values,
    get_node_parameter_values,
    get_node_schemas,
)
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory


def test_get_node_schemas_returns_every_concrete_node_type():
    titles = {schema["title"] for schema in get_node_schemas()}
    assert {"StartNode", "EndNode", "LLMResponseWithPrompt", "RouterNode"} <= titles


@pytest.mark.django_db()
def test_get_node_parameter_values_is_team_scoped():
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

    provider_ids = {option["value"] for option in values["LlmProviderId"]}
    assert mine.id in provider_ids
    assert theirs.id not in provider_ids


@pytest.mark.django_db()
def test_get_node_default_values_pairs_a_provider_with_a_type_matching_model():
    team = TeamWithUsersFactory.create()
    provider = LlmProviderFactory.create(team=team, type="openai")
    # Own the model row rather than leaning on the global seed rows (team=None) that migration
    # 0021 installs: any `django_db(transaction=True)` test flushes those away for the rest of
    # the process, so relying on them makes this test's outcome depend on execution order.
    model = LlmProviderModelFactory.create(team=team, type="openai")

    defaults = get_node_default_values(
        list(LlmProvider.objects.filter(team=team).values("id", "name", "type")),
        LlmProviderModel.objects.filter(team=team),
    )

    assert defaults["llm_provider_id"] == provider.id
    assert defaults["llm_provider_model_id"] == model.id


@pytest.mark.django_db()
def test_pipeline_builder_context_still_populated(client):
    """Regression guard on the extraction: the builder view still gets its three context keys."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    LlmProviderFactory.create(team=team)
    pipeline = PipelineFactory.create(team=team)
    client.force_login(user)

    response = client.get(reverse("pipelines:edit", kwargs={"team_slug": team.slug, "pk": pipeline.id}))

    assert response.status_code == 200
    assert response.context["node_schemas"]
    assert response.context["parameter_values"]["LlmProviderId"]
    assert "llm_provider_id" in response.context["default_values"]
