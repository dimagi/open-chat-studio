"""Every resource paged by ``updated_at`` needs an index leading with it, or each page costs a
top-N sort of the whole scoped set."""

import pytest
from django.apps import apps

from apps.teams.export import manifest

# Class (c) models: a chatbot-scoped sync serves these empty, and they are small enough in a
# full-team sync that the sort is free. Reviewed when a model moves between scope classes.
UNINDEXED_UPDATED_AT_MODELS = frozenset(
    {
        "analysis.transcriptanalysis",
        "evaluations.datasetautopopulationrule",
        "evaluations.evaluationconfig",
        "evaluations.evaluationdataset",
        "evaluations.evaluationmessage",
        "evaluations.evaluationrun",
        "evaluations.evaluationrunaggregate",
        "evaluations.evaluator",
        "evaluations.evaluatortagrule",
        "human_annotations.annotation",
        "human_annotations.annotationitem",
        "human_annotations.annotationqueue",
        "human_annotations.annotationqueueaggregate",
    }
)


def _leads_with_updated_at(model) -> bool:
    return any(list(index.fields)[:2] == ["updated_at", "id"] for index in model._meta.indexes)


@pytest.mark.parametrize(
    "entry",
    [e for e in manifest.MANIFEST_ENTRIES if e.cursor == "updated_at_id"],
    ids=lambda e: e.resource,
)
def test_updated_at_resources_have_a_matching_index(entry):
    model = apps.get_model(*entry.model.split("."))
    if entry.model in UNINDEXED_UPDATED_AT_MODELS:
        pytest.skip(f"{entry.model} is deliberately unindexed")
    assert _leads_with_updated_at(model), f"{entry.model} needs models.Index(fields=['updated_at', 'id'])"


def test_unindexed_list_only_names_synced_models():
    synced = {e.model for e in manifest.MANIFEST_ENTRIES}
    assert synced >= UNINDEXED_UPDATED_AT_MODELS
