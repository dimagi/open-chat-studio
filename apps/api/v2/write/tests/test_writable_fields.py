"""The write surface must keep pace with two things it is defined against.

PATCH /chatbots/{id}/ is meant to be the API twin of the chatbot settings page, and also to cover
everything a published version snapshots. Those are two different sets that happen to coincide
today, and a new field can be added to either one alone -- so each is asserted separately, and a
failure here says which of the two drifted.

Both are compared as *model* attribute names: `_writable_model_fields` reads each field's `source`,
which is where the API's `<resource>_id` names map back to the FK they write.
"""

import pytest
import yaml
from rest_framework import serializers

from apps.api.v2.write.fields import TeamScopedRelatedField
from apps.api.v2.write.serializers import (
    ChatbotCreateSerializer,
    ChatbotWriteSerializer,
    RejectsUnknownKeys,
)
from apps.chatbots.forms import ChatbotSettingsForm
from apps.experiments.models import Experiment


def _writable_model_fields(serializer) -> set[str]:
    return {field.source for field in serializer.fields.values() if not field.read_only}


def test_patch_covers_every_versioned_field_except_the_pipeline():
    """`VERSIONED_CONTENT_FIELDS` is what a published version snapshots. Everything in it except
    `pipeline` -- which has its own façade at /chatbots/{id}/pipeline/* -- is writable here, so a
    new versioned field has to be an explicit decision rather than an omission.

    `participant_allowlist` is the one deliberate exception: its column and versioning stay in
    place while the participant allowlist feature is removed in phases, but nothing writes to it
    through this API any more."""
    assert _writable_model_fields(ChatbotWriteSerializer()) == (
        Experiment.VERSIONED_CONTENT_FIELDS - {"pipeline", "participant_allowlist"}
    )


def test_patch_writes_exactly_what_the_settings_form_edits():
    """The write API mimics the UI's own form rather than the inspect response: inspecting and
    editing are different jobs, and inspect returns plenty that is not writable. A field added to
    the settings page therefore has to be added here too.

    `participant_allowlist` is excluded here too: the settings form still edits it until it is
    removed from the UI, but the API no longer accepts it."""
    assert _writable_model_fields(ChatbotWriteSerializer()) == set(ChatbotSettingsForm.Meta.fields) - {
        "participant_allowlist"
    }


def _related_fields(serializer):
    """Every relational field on the serializer."""
    for name, field in serializer.fields.items():
        if isinstance(field, serializers.RelatedField):
            yield name, field


def test_every_reference_on_the_patch_body_is_team_scoped():
    """The two tests above compare field *names*, so a relation declared as a plain
    `PrimaryKeyRelatedField(queryset=Model.objects.all())` satisfies both while accepting another
    team's id. Team scoping is the security boundary here, so it is asserted structurally rather
    than left to whoever adds the next FK."""
    for name, field in _related_fields(ChatbotWriteSerializer()):
        assert isinstance(field, TeamScopedRelatedField), f"{name} is a relation but is not team-scoped"


def _v2_components(pytestconfig) -> dict:
    """The committed v2 schema's component schemas.

    Read from the file rather than regenerated: `test_schema_is_up_to_date_and_valid` already pins
    it to what generation produces, and reading is a great deal cheaper.
    """
    with open(f"{pytestconfig.rootdir}/api-schemas/v2.yml") as schema:
        return yaml.safe_load(schema)["components"]["schemas"]


@pytest.mark.parametrize(
    ("component", "serializer"),
    [
        pytest.param("ChatbotCreate", ChatbotCreateSerializer, id="create-body"),
        pytest.param("PatchedChatbotWrite", ChatbotWriteSerializer, id="patch-body"),
    ],
)
def test_the_schema_closes_each_body_that_rejects_unknown_keys(pytestconfig, component, serializer):
    """`RejectsUnknownKeys` 400s on a key it does not declare, but OpenAPI permits extra properties
    by default -- so left open, a generated client or a validator would accept a body the API
    refuses, and the consumer this API is built for reads the schema rather than the prose.

    The declared properties are compared against `serializer().fields`, which is the set
    `RejectsUnknownKeys` itself checks against, so the two cannot drift apart.
    """
    schema = _v2_components(pytestconfig)[component]

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(serializer().fields)


def test_every_serializer_that_rejects_unknown_keys_is_closed_in_the_schema(pytestconfig):
    """`mirror_unknown_key_rejection` derives component names from the class names, so a serializer
    drf-spectacular happens to name differently would silently keep OpenAPI's permissive default.
    `Patched` is the prefix it gives a PATCH body."""
    closed = {
        name for name, schema in _v2_components(pytestconfig).items() if schema.get("additionalProperties") is False
    }

    assert {name.removeprefix("Patched") for name in closed} == {
        cls.__name__.removesuffix("Serializer") for cls in RejectsUnknownKeys.__subclasses__()
    }
