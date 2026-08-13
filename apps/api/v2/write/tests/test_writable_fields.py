"""The write surface must keep pace with Experiment's versioned fields.

`Experiment.VERSIONED_CONTENT_FIELDS` is what a published version snapshots. Everything in it
except `pipeline` -- which has its own façade at /chatbots/{id}/pipeline/* -- is writable through
PATCH /chatbots/{id}/, which makes "what inspect shows you is what you can write" true by
construction. A new versioned field therefore has to be an explicit decision here.
"""

from rest_framework import serializers

from apps.api.v2.write.serializers import ChatbotWriteSerializer
from apps.experiments.models import Experiment


def _writable_model_fields(serializer) -> set[str]:
    """Every model attribute the serializer can write, following nesting and `source`."""
    covered = set()
    for field in serializer.fields.values():
        if field.read_only:
            continue
        if isinstance(field, serializers.BaseSerializer):
            covered |= _writable_model_fields(field)
        else:
            covered.add(field.source)
    return covered


def test_patch_covers_every_versioned_field_except_the_pipeline():
    assert _writable_model_fields(ChatbotWriteSerializer()) == (Experiment.VERSIONED_CONTENT_FIELDS - {"pipeline"})
