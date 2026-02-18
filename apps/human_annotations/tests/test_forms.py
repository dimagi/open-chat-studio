import pytest

from apps.human_annotations.forms import build_annotation_form
from apps.human_annotations.models import AnnotationQueue
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.mark.django_db()
def test_build_annotation_form_required_by_default(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={
            "score": {"type": "int", "description": "Score"},
            "notes": {"type": "string", "description": "Notes"},
        },
        created_by=team.members.first(),
    )
    FormClass = build_annotation_form(queue)
    form = FormClass()
    assert form.fields["score"].required is True
    assert form.fields["notes"].required is True


@pytest.mark.django_db()
def test_build_annotation_form_optional_fields(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={
            "score": {"type": "int", "description": "Score", "required": True},
            "notes": {"type": "string", "description": "Notes", "required": False},
            "rating": {"type": "float", "description": "Rating", "required": False},
            "category": {"type": "choice", "description": "Cat", "choices": ["a", "b"], "required": False},
        },
        created_by=team.members.first(),
    )
    FormClass = build_annotation_form(queue)
    form = FormClass()
    assert form.fields["score"].required is True
    assert form.fields["notes"].required is False
    assert form.fields["rating"].required is False
    assert form.fields["category"].required is False


@pytest.mark.django_db()
def test_optional_fields_accept_empty_submission(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={
            "score": {"type": "int", "description": "Score"},
            "notes": {"type": "string", "description": "Notes", "required": False},
        },
        created_by=team.members.first(),
    )
    FormClass = build_annotation_form(queue)
    form = FormClass(data={"score": "5", "notes": ""})
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_required_fields_reject_empty_submission(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={
            "score": {"type": "int", "description": "Score"},
            "notes": {"type": "string", "description": "Notes"},
        },
        created_by=team.members.first(),
    )
    FormClass = build_annotation_form(queue)
    form = FormClass(data={"score": "", "notes": ""})
    assert not form.is_valid()
    assert "score" in form.errors
    assert "notes" in form.errors
