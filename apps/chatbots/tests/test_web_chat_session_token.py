import pytest
from django.core import signing
from django.urls import reverse
from waffle.testutils import override_flag

from apps.experiments.decorators import CHAT_SESSION_ACCESS_COOKIE, CHAT_SESSION_ACCESS_SALT
from apps.experiments.models import Participant, SessionStatus
from apps.utils.factories.experiment import ExperimentSessionFactory


def _set_access_cookie(client, session):
    """Set a valid session access cookie on the test client."""
    experiment = session.experiment
    value = {
        "experiment_id": str(experiment.public_id),
        "session_id": str(session.external_id),
        "participant_id": session.participant_id,
        "user_id": session.participant.user_id,
    }
    signed = signing.get_cookie_signer(salt=CHAT_SESSION_ACCESS_SALT).sign_object(value)
    client.cookies[CHAT_SESSION_ACCESS_COOKIE] = signed


def _chat_url(session):
    return reverse(
        "chatbots:chatbot_chat",
        args=[session.experiment.team.slug, session.experiment.public_id, session.external_id],
    )


@pytest.mark.django_db()
class TestWebChatSessionTokenOptOut:
    @pytest.fixture()
    def session(self):
        session = ExperimentSessionFactory.create(
            experiment__pre_survey=None,
            status=SessionStatus.ACTIVE,
        )
        participant = Participant.objects.create(
            team=session.team,
            identifier="anon@test.com",
            platform="web",
        )
        session.participant = participant
        session.save()
        return session

    @override_flag("flag_chat_widget", active=True)
    def test_session_token_required_cleared_when_widget_flag_active(self, client, session):
        assert session.session_token_required is True
        _set_access_cookie(client, session)
        response = client.get(_chat_url(session))
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.session_token_required is False

    @override_flag("flag_chat_widget", active=False)
    def test_session_token_required_unchanged_when_widget_flag_inactive(self, client, session):
        assert session.session_token_required is True
        _set_access_cookie(client, session)
        response = client.get(_chat_url(session))
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.session_token_required is True
