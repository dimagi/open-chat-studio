"""`allowed_chatbots` is an authorization boundary, so every edit to it is recorded.

`field_audit` registers `m2m_changed` handlers for audited ManyToManyFields, so `.add()`, `.remove()`
and `.set()` all produce events. Auditing is off under test (`FIELD_AUDIT_ENABLED = not IS_TESTING`),
hence the explicit `enable_audit()`.
"""

import pytest
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.oauth.models import OAuth2Application
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def application(db):
    team = TeamWithUsersFactory.create()
    app = OAuth2Application.objects.create(
        name="machine-app",
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    return app


def _audited_deltas(application):
    events = AuditEvent.objects.by_model(OAuth2Application).filter(object_pk=application.pk)
    return [event.delta for event in events if "allowed_chatbots" in (event.delta or {})]


@pytest.mark.django_db()
@pytest.mark.parametrize("write", ["add", "remove", "set"])
def test_allowed_chatbots_writes_are_audited(application, write):
    chatbot = ExperimentFactory.create(team=application.team)
    if write == "remove":
        application.allowed_chatbots.add(chatbot)

    with enable_audit():
        if write == "add":
            application.allowed_chatbots.add(chatbot)
        elif write == "remove":
            application.allowed_chatbots.remove(chatbot)
        else:
            application.allowed_chatbots.set([chatbot])

    assert _audited_deltas(application), "no audit event recorded for allowed_chatbots"
