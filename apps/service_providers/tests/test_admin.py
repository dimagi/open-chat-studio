import pytest

from apps.service_providers.admin import LlmProviderModelAdmin
from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory

PIPELINE_NAME = "Referencing pipeline"


def _related_nodes(provider_model):
    admin = LlmProviderModelAdmin(LlmProviderModel, admin_site=None)
    return admin.related_nodes(provider_model)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("spellings", "expected_listings"),
    [
        pytest.param([int], 1, id="id-stored-as-int"),
        pytest.param([str], 1, id="id-stored-as-str"),
        pytest.param([int, str], 1, id="two-nodes-listed-once"),
        pytest.param([], 0, id="pipeline-referencing-nothing"),
    ],
)
def test_related_nodes_lists_each_referencing_pipeline_once(spellings, expected_listings):
    """How many times the owning pipeline appears, per node referencing the model.

    The FK column is populated whether the id was stored as an int or a str.
    """
    provider_model = LlmProviderModelFactory.create()
    pipeline = PipelineFactory.create(team=provider_model.team, name=PIPELINE_NAME)
    for spelling in spellings:
        NodeFactory.create(pipeline=pipeline, params={"llm_provider_model_id": spelling(provider_model.id)})

    assert _related_nodes(provider_model).count(PIPELINE_NAME) == expected_listings


@pytest.mark.django_db()
def test_related_nodes_escapes_the_pipeline_name():
    """The name is team-supplied, so it is data in the rendered link, never markup."""
    provider_model = LlmProviderModelFactory.create()
    pipeline = PipelineFactory.create(team=provider_model.team, name="<script>alert(1)</script>")
    NodeFactory.create(pipeline=pipeline, params={"llm_provider_model_id": provider_model.id})

    rendered = _related_nodes(provider_model)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
