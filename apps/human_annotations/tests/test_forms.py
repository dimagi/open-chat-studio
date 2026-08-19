import json

import pytest

from apps.evaluations.models import DatasetCreationStatus
from apps.human_annotations.forms import AnnotationQueueForm, ImportFromDatasetForm, build_annotation_form
from apps.human_annotations.models import Annotation, AnnotationQueue, AnnotationStatus
from apps.utils.factories.evaluations import EvaluationDatasetFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


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
    assert queue.schema["score"]["required"] is True  # normalized storage makes the default explicit


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
class TestBinaryAnnotationForm:
    def test_binary_field_renders_select_and_cleans_to_int(self):
        queue = AnnotationQueueFactory(binary_schema=True)
        form_class = build_annotation_form(queue)

        form = form_class(data={"correct": "1"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["correct"] == 1

        form = form_class(data={"correct": "0"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["correct"] == 0

    def test_binary_field_shows_labels_not_integers(self):
        queue = AnnotationQueueFactory(binary_schema=True)
        form_class = build_annotation_form(queue)
        choice_labels = [label for _, label in form_class.base_fields["correct"].choices]
        assert "Correct" in choice_labels
        assert "Incorrect" in choice_labels

    def test_binary_field_rejects_out_of_range_value(self):
        queue = AnnotationQueueFactory(binary_schema=True)
        form_class = build_annotation_form(queue)
        form = form_class(data={"correct": "2"})
        assert not form.is_valid()


@pytest.mark.django_db()
def test_queue_schema_accepts_binary_definition():
    form = AnnotationQueueForm(
        data={
            "name": "Binary queue",
            "num_reviews_required": 1,
            "schema": json.dumps(
                {
                    "correct": {
                        "type": "binary",
                        "description": "Was it correct?",
                        "true_label": "Yes",
                        "false_label": "No",
                    }
                }
            ),
        }
    )
    assert form.is_valid(), form.errors


@pytest.mark.parametrize(
    ("submitted", "expected_true", "expected_false"),
    [
        pytest.param(
            {"type": "binary", "description": "Was it correct?"},
            "True",
            "False",
            id="missing-labels-stored-with-defaults",
        ),
        pytest.param(
            {"type": "binary", "description": "Was it correct?", "true_label": " Yes ", "false_label": " No "},
            "Yes",
            "No",
            id="padded-labels-stored-trimmed",
        ),
    ],
)
@pytest.mark.django_db()
def test_queue_schema_stores_normalized_binary_definition(submitted, expected_true, expected_false):
    # The stored schema must be the pydantic-normalized form, not the raw submission:
    # a raw dict diverging from what the builder serializes makes a locked queue uneditable.
    form = AnnotationQueueForm(
        data={
            "name": "Binary queue",
            "num_reviews_required": 1,
            "schema": json.dumps({"correct": submitted}),
        }
    )
    assert form.is_valid(), form.errors
    stored = form.cleaned_data["schema"]["correct"]
    assert stored["true_label"] == expected_true
    assert stored["false_label"] == expected_false


@pytest.mark.django_db()
def test_locked_queue_with_label_free_stored_schema_accepts_builder_schema():
    # A binary schema stored without label keys (written before normalization, or via a
    # non-builder client) must still accept the builder's canonical serialization once
    # locked, otherwise the whole settings form becomes uneditable.
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Queue",
        schema={"correct": {"type": "binary", "description": "Was it correct?"}},
        created_by=user,
    )
    item = AnnotationItemFactory.create(queue=queue, team=team)
    Annotation.objects.create(
        item=item,
        team=team,
        reviewer=user,
        data={"correct": 1},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 1

    form = AnnotationQueueForm(
        instance=queue,
        data={
            "name": queue.name,
            "num_reviews_required": queue.num_reviews_required,
            "schema": json.dumps(
                {
                    "correct": {
                        "type": "binary",
                        "description": "Was it correct?",
                        "true_label": "True",
                        "false_label": "False",
                    }
                }
            ),
        },
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_locked_binary_schema_roundtrip_is_a_valid_edit():
    # The exact schema the builder serializes must pass the locked-schema check,
    # or locked queues become permanently uneditable.
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    queue = AnnotationQueueFactory.create(binary_schema=True, team=team, created_by=user)
    item = AnnotationItemFactory.create(queue=queue, team=team)
    # Mirrors the locked-schema setup in test_views.py: a SUBMITTED annotation is what
    # drives review_count up via Annotation.save(), which is what actually engages the lock.
    Annotation.objects.create(
        item=item,
        team=team,
        reviewer=user,
        data={"correct": 1},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 1

    form = AnnotationQueueForm(
        instance=queue,
        data={
            "name": queue.name,
            "num_reviews_required": queue.num_reviews_required,
            "schema": json.dumps(queue.schema),
        },
    )
    assert form.is_valid(), form.errors


# === ImportFromDatasetForm ===


@pytest.mark.django_db()
def test_import_from_dataset_form_shows_completed_datasets_for_team(team):
    dataset = EvaluationDatasetFactory.create(team=team)  # default status is COMPLETED
    form = ImportFromDatasetForm(team=team)
    assert dataset in form.fields["dataset"].queryset


@pytest.mark.django_db()
def test_import_from_dataset_form_excludes_non_completed_datasets(team):
    EvaluationDatasetFactory.create(team=team, status=DatasetCreationStatus.PENDING)
    EvaluationDatasetFactory.create(team=team, status=DatasetCreationStatus.PROCESSING)
    EvaluationDatasetFactory.create(team=team, status=DatasetCreationStatus.FAILED)
    form = ImportFromDatasetForm(team=team)
    assert form.fields["dataset"].queryset.count() == 0


@pytest.mark.django_db()
def test_import_from_dataset_form_excludes_other_team_datasets(team):
    other_team = TeamWithUsersFactory.create()
    EvaluationDatasetFactory.create(team=other_team)
    form = ImportFromDatasetForm(team=team)
    assert form.fields["dataset"].queryset.count() == 0
