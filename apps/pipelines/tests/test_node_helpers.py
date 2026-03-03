import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.nodes.helpers import temporary_session
from apps.teams.models import Team
from apps.utils.factories.team import TeamFactory, UserFactory


@pytest.mark.django_db()
def test_temporary_session_is_temporary():
    session_id = None
    user = UserFactory()
    team: Team = TeamFactory()  # ty: ignore[invalid-assignment]
    with temporary_session(team, user.id) as session:
        session_id = session.id
        message = session.chat.messages.create(message_type=ChatMessageType.HUMAN, content="Hello, world!")

    assert not ExperimentSession.objects.filter(id=session_id).exists()
    assert not ChatMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
def test_temporary_session_rolls_back_on_error():
    user = UserFactory()

    def _run_with_temp_session():
        team: Team = TeamFactory()  # ty: ignore[invalid-assignment]
        with temporary_session(team, user.id):
            raise Exception("error")

    with pytest.raises(Exception, match="error"):
        _run_with_temp_session()

    assert ExperimentSession.objects.count() == 0
