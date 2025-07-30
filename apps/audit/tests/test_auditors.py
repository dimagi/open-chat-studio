from dataclasses import dataclass

from field_audit.models import USER_TYPE_REQUEST

from apps.audit.auditors import AuditContextProvider
from apps.teams.utils import current_team


def test_change_context():
    request = AuthedRequest()
    # Force the team to be None
    with current_team(None):
        assert AuditContextProvider().change_context(request) == {
            "user_type": USER_TYPE_REQUEST,
            "username": request.user.username,
        }


def test_change_context_returns_none_without_request():
    assert AuditContextProvider().change_context(None)["user_type"] != USER_TYPE_REQUEST


def test_change_context_returns_none_without_request_with_team():
    with current_team(Team()):
        context = AuditContextProvider().change_context(None)
        assert context["user_type"] != USER_TYPE_REQUEST
        assert context["team"] == 17


def test_change_context_returns_value_for_unauthorized_req():
    request = AuthedRequest(auth=False)
    assert "user_type" not in AuditContextProvider().change_context(request)


def test_change_context_returns_value_for_unauthorized_team_req():
    request = AuthedRequest(auth=False)
    with current_team(Team()):
        assert AuditContextProvider().change_context(request) == {"team": 17}


def test_change_context_returns_value_for_authorized_team_req():
    request = AuthedRequest(auth=True)
    with current_team(Team()):
        assert AuditContextProvider().change_context(request) == {
            "user_type": USER_TYPE_REQUEST,
            "username": "test@example.com",
            "team": 17,
        }


class AuthedRequest:
    def __init__(self, auth=True, session=None):
        self.user = User()
        self.session = session or {}
        self.user.is_authenticated = auth


@dataclass
class User:
    username: str = "test@example.com"
    is_authenticated: str = True


@dataclass
class Team:
    id: int = 17
    slug: str = "seventeen"
