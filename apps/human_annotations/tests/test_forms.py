import json

import pytest

from apps.human_annotations.forms import AnnotationQueueForm, build_annotation_form
from apps.human_annotations.models import Annotation, AnnotationQueue, AnnotationStatus
from apps.utils.factories.human_annotations import AnnotationItemFactory
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
def test_queue_form_preserves_required_false(team):
    """Submitting the queue form with required=false in schema should persist to DB."""
    schema = {
        "score": {"type": "int", "description": "Score"},
        "notes": {"type": "string", "description": "Notes", "required": False},
    }
    form = AnnotationQueueForm(
        data={
            "name": "Test Queue",
            "description": "",
            "schema": json.dumps(schema),
            "num_reviews_required": 1,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["schema"]["notes"]["required"] is False

    # Save via the form (like the view does) and verify DB round-trip
    queue = form.save(commit=False)
    queue.team = team
    queue.created_by = team.members.first()
    queue.save()

    queue.refresh_from_db()
    assert queue.schema["notes"]["required"] is False
    assert "required" not in queue.schema["score"]  # not included when true (default)


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


@pytest.mark.django_db()
def test_locked_queue_form_allows_required_change(team):
    """When annotations exist, changing 'required' should be accepted."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=user,
    )
    item = AnnotationItemFactory(queue=queue, team=team)
    Annotation.objects.create(
        item=item,
        team=team,
        reviewer=user,
        data={"score": 4},
        status=AnnotationStatus.SUBMITTED,
    )

    new_schema = {"score": {"type": "int", "description": "Score", "ge": 1, "le": 5, "required": False}}
    form = AnnotationQueueForm(
        instance=queue,
        data={
            "name": queue.name,
            "description": "",
            "schema": json.dumps(new_schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["schema"]["score"]["required"] is False


@pytest.mark.django_db()
def test_locked_queue_form_rejects_structural_change(team):
    """When annotations exist, changing field type/constraints should be rejected."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=user,
    )
    item = AnnotationItemFactory(queue=queue, team=team)
    Annotation.objects.create(
        item=item,
        team=team,
        reviewer=user,
        data={"score": 4},
        status=AnnotationStatus.SUBMITTED,
    )

    # Try to change the type
    bad_schema = {"score": {"type": "float", "description": "Score", "ge": 1, "le": 5}}
    form = AnnotationQueueForm(
        instance=queue,
        data={
            "name": queue.name,
            "description": "",
            "schema": json.dumps(bad_schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert not form.is_valid()
    assert "schema" in form.errors
