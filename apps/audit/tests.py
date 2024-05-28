from unittest import mock

from field_audit.models import USER_TYPE_REQUEST

from apps.audit.auditors import RequestAuditor


def test_change_context():
    request = AuthedRequest()
    assert RequestAuditor().change_context(request) == {
        "user_type": USER_TYPE_REQUEST,
        "username": request.user.username,
    }


def test_change_context_returns_none_without_request():
    assert RequestAuditor().change_context(None) is None


def test_change_context_returns_value_for_unauthorized_req():
    request = AuthedRequest(auth=False)
    assert RequestAuditor().change_context(request) == {}


@mock.patch("apps.audit.auditors._get_hijack_username", return_value="admin@example.com")
def test_change_context_hijacked_request(_):
    request = AuthedRequest(session={"hijack_history": [1]})
    assert RequestAuditor().change_context(request) == {
        "user_type": USER_TYPE_REQUEST,
        "username": "admin@example.com",
        "as_username": request.user.username,
    }


@mock.patch("apps.audit.auditors._get_hijack_username", return_value=None)
def test_change_context_hijacked_request__no_hijacked_user(_):
    request = AuthedRequest(session={"hijack_history": [1]})
    assert RequestAuditor().change_context(request) == {
        "user_type": USER_TYPE_REQUEST,
        "username": "test@example.com",
    }


def test_change_context_hijacked_request__bad_hijack_history():
    request = AuthedRequest(session={"hijack_history": ["not a number"]})
    assert RequestAuditor().change_context(request) == {
        "user_type": USER_TYPE_REQUEST,
        "username": "test@example.com",
    }


class AuthedRequest:
    class User:
        username = "test@example.com"
        is_authenticated = True

    def __init__(self, auth=True, session=None):
        self.user = self.User()
        self.session = session or {}
        self.user.is_authenticated = auth
