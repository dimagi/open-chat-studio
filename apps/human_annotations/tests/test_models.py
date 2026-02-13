import django.db
import pytest

from apps.human_annotations.models import AnnotationSchema
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.mark.django_db()
def test_create_annotation_schema(team):
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Quality Review",
        schema={
            "quality_score": {"type": "int", "description": "Overall quality 1-5", "ge": 1, "le": 5},
            "category": {
                "type": "choice",
                "description": "Response category",
                "choices": ["correct", "partially_correct", "incorrect"],
            },
            "notes": {"type": "string", "description": "Additional notes"},
        },
    )
    assert schema.id is not None
    assert schema.name == "Quality Review"
    assert len(schema.schema) == 3
    assert schema.schema["quality_score"]["type"] == "int"


@pytest.mark.django_db()
def test_annotation_schema_unique_name_per_team(team):
    AnnotationSchema.objects.create(team=team, name="Test Schema", schema={})
    with pytest.raises(django.db.IntegrityError):
        AnnotationSchema.objects.create(team=team, name="Test Schema", schema={})


@pytest.mark.django_db()
def test_annotation_schema_get_field_definitions(team):
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Test",
        schema={
            "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        },
    )
    field_defs = schema.get_field_definitions()
    assert "score" in field_defs
    assert field_defs["score"].type == "int"
    assert field_defs["score"].ge == 1
    assert field_defs["score"].le == 5
