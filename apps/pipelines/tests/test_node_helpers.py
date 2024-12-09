import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.nodes.helpers import temporary_session
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_temporary_session_is_temporary():
    session_id = None
    with temporary_session(TeamFactory()) as session:
        session_id = session.id
        message = session.chat.messages.create(message_type=ChatMessageType.HUMAN, content="Hello, world!")

    assert not ExperimentSession.objects.filter(id=session_id).exists()
    assert not ChatMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
def test_temporary_session_rolls_back_on_error():
    def _run_with_temp_session():
        with temporary_session(TeamFactory()):
            raise Exception("error")

    with pytest.raises(Exception, match="error"):
        _run_with_temp_session()

    assert ExperimentSession.objects.count() == 0
