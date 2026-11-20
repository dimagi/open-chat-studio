"""How the chat export queries the database: the session filter once, message ids in pages."""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import generate_export_rows
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_session_filter_runs_once_across_chunks_and_pages():
    """The filtered sessions are resolved to chat ids once, however many chunks and id pages follow."""
    session = ExperimentSessionFactory.create()
    for i in range(9):
        ChatMessage.objects.create(chat=session.chat, content=f"m{i}", message_type=ChatMessageType.HUMAN)
    with (
        patch("apps.experiments.export.EXPORT_CHUNK_SIZE", 2),
        patch("apps.experiments.export.ID_PAGE_SIZE", 4),
        CaptureQueriesContext(connection) as queries,
    ):
        rows = list(generate_export_rows(session.experiment, session.experiment.sessions.all()))

    resolve_sql = 'SELECT "experiments_experimentsession"."chat_id"'
    assert sum(1 for query in queries if query["sql"].startswith(resolve_sql)) == 1
    assert [row[0] for row in rows[1:]] == sorted(session.chat.messages.values_list("id", flat=True))


@pytest.mark.django_db()
def test_export_keeps_message_pk_order_across_sessions_and_pages():
    """Only the filtered sessions' messages are exported, interleaved in pk order across id pages."""
    sessions = ExperimentSessionFactory.create_batch(3, experiment=ExperimentFactory.create())
    experiment = sessions[0].experiment
    for i in range(4):
        for session in sessions:
            ChatMessage.objects.create(chat=session.chat, content=f"m{i}", message_type=ChatMessageType.HUMAN)
    exported = ExperimentSession.objects.filter(id__in=[sessions[0].id, sessions[2].id])

    with patch("apps.experiments.export.EXPORT_CHUNK_SIZE", 2), patch("apps.experiments.export.ID_PAGE_SIZE", 3):
        rows = list(generate_export_rows(experiment, exported))

    expected = ChatMessage.objects.filter(chat__experiment_session__in=exported).order_by("pk")
    assert [row[0] for row in rows[1:]] == list(expected.values_list("id", flat=True))
