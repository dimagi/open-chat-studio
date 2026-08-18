import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from apps.experiments.decorators import experiment_session_view, require_transcript_access
from apps.teams.decorators import ENFORCES_TEAM_AUTH_ATTR
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.user import UserFactory


@require_transcript_access
def _view(request):
    return "rendered"


def _request(user, session):
    request = RequestFactory().get("/")
    request.user = user
    request.experiment_session = session
    return request


@pytest.mark.django_db()
def test_anonymous_callers_are_refused_even_for_a_participant_without_a_user():
    """`AnonymousUser.id` and an anonymous participant's `user_id` are both None.

    Ownership must not fall out of comparing two Nones, or dropping
    `login_and_team_required` from a view would open every anonymous session's transcript.
    """
    session = ExperimentSessionFactory.create()
    assert session.participant.user_id is None

    with pytest.raises(PermissionDenied):
        _view(_request(AnonymousUser(), session))


@pytest.mark.django_db()
def test_session_owner_without_chat_view_chat_is_allowed():
    session = ExperimentSessionFactory.create()
    user = UserFactory.create()
    session.participant.user = user
    session.participant.save()
    assert not user.has_perm("chat.view_chat")

    assert _view(_request(user, session)) == "rendered"


def test_experiment_session_view_does_not_claim_to_enforce_team_auth():
    """Resolving team-scoped objects is not authorisation.

    Stamping the marker would let apps/teams/tests/test_view_auth_guard.py pass for a view
    that only resolves the session, hiding a missing `login_and_team_required`.
    """

    @experiment_session_view
    def view(request, team_slug, experiment_id, session_id):
        return None

    assert not getattr(view, ENFORCES_TEAM_AUTH_ATTR, False)
