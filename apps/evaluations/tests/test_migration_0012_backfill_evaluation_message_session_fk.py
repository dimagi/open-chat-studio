import importlib
import uuid

import pytest
from django.apps import apps
from django.db import connection

from apps.utils.factories.evaluations import EvaluationMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

_migration = importlib.import_module("apps.evaluations.migrations.0012_backfill_evaluation_message_session_fk")
backfill_session_fk = _migration.backfill_session_fk


class FakeSchemaEditor:
    connection = connection


def _run_migration():
    backfill_session_fk(apps, FakeSchemaEditor())


@pytest.mark.django_db()
def test_backfill_sets_session_fk_when_metadata_matches():
    session = ExperimentSessionFactory()
    msg = EvaluationMessageFactory(metadata={"session_id": str(session.external_id)})

    _run_migration()

    msg.refresh_from_db()
    assert msg.session_id == session.pk


@pytest.mark.django_db()
def test_backfill_no_match_leaves_session_null():
    msg = EvaluationMessageFactory(metadata={"session_id": str(uuid.uuid4())})

    _run_migration()

    msg.refresh_from_db()
    assert msg.session_id is None


@pytest.mark.django_db()
def test_backfill_skips_messages_without_session_id_in_metadata():
    msg = EvaluationMessageFactory(metadata={"other_key": "value"})

    _run_migration()

    msg.refresh_from_db()
    assert msg.session_id is None


@pytest.mark.django_db()
def test_backfill_skips_non_dict_metadata():
    # Store a JSON array (non-dict) directly via raw SQL to bypass model validation
    msg = EvaluationMessageFactory(metadata={})
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE evaluations_evaluationmessage SET metadata = %s::jsonb WHERE id = %s",
            ['["not", "a", "dict"]', msg.pk],
        )

    _run_migration()

    msg.refresh_from_db()
    assert msg.session_id is None


@pytest.mark.django_db()
def test_backfill_skips_messages_already_having_session():
    session = ExperimentSessionFactory()
    another_session = ExperimentSessionFactory()
    # Message already has a session set; metadata points to a different session
    msg = EvaluationMessageFactory(
        session=another_session,
        metadata={"session_id": str(session.external_id)},
    )

    _run_migration()

    msg.refresh_from_db()
    # Should not be overwritten since session__isnull=True filter excludes it
    assert msg.session_id == another_session.pk


@pytest.mark.django_db()
def test_backfill_handles_multiple_messages_for_same_session():
    session = ExperimentSessionFactory()
    msgs = EvaluationMessageFactory.create_batch(3, metadata={"session_id": str(session.external_id)})

    _run_migration()

    for msg in msgs:
        msg.refresh_from_db()
        assert msg.session_id == session.pk
