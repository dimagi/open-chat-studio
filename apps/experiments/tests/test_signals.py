import pytest

from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestDeleteChatOnSessionDelete:
    def test_deleting_session_deletes_associated_chat(self):
        session = ExperimentSessionFactory()
        chat_id = session.chat_id

        session.delete()

        assert not Chat.objects.filter(id=chat_id).exists()

    def test_deleting_chat_does_not_cause_error(self):
        """Deleting a Chat cascades to the session, which fires the signal.
        The signal should handle the already-deleted Chat gracefully."""
        session = ExperimentSessionFactory()
        chat_id = session.chat_id
        session_id = session.id

        Chat.objects.filter(id=chat_id).delete()

        assert not ExperimentSession.objects.filter(id=session_id).exists()
        assert not Chat.objects.filter(id=chat_id).exists()

    def test_bulk_deleting_sessions_deletes_chats(self):
        sessions = ExperimentSessionFactory.create_batch(3)
        chat_ids = [s.chat_id for s in sessions]
        session_ids = [s.id for s in sessions]

        ExperimentSession.objects.filter(id__in=session_ids).delete()

        assert not Chat.objects.filter(id__in=chat_ids).exists()
