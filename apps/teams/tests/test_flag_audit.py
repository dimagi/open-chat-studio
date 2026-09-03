"""The request-only waffle columns stay on the model (they come from waffle's abstract
base), so a write to a neutralised field remains possible. The audit log is the tripwire
that records such a write.

Auditing is off under test (`FIELD_AUDIT_ENABLED = not IS_TESTING`), hence the explicit
`enable_audit()`.
"""

import pytest
from django.contrib.auth.models import Group
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.teams.models import Flag


def _audited_deltas(flag, field):
    """Deltas of the flag's audit events that record a change to `field`."""
    events = AuditEvent.objects.by_model(Flag).filter(object_pk=flag.pk)
    return [event.delta for event in events if field in (event.delta or {})]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("superusers", False, id="superusers"),
        pytest.param("staff", True, id="staff"),
        pytest.param("authenticated", True, id="authenticated"),
        pytest.param("testing", True, id="testing"),
        pytest.param("rollout", True, id="rollout"),
        pytest.param("percent", 50, id="percent"),
        pytest.param("languages", "en", id="languages"),
    ],
)
def test_writes_to_request_only_fields_are_audited(field, value):
    """A direct model write to any neutralised field leaves an audit event."""
    Flag.objects.create(name="flag_audit_probe")

    with enable_audit():
        flag = Flag.objects.get(name="flag_audit_probe")
        setattr(flag, field, value)
        flag.save()

    assert _audited_deltas(flag, field), f"no audit event recorded for {field}"


@pytest.mark.django_db()
def test_group_writes_are_audited():
    """M2M writes to `groups` leave an audit event, like `teams` and `users` already do."""
    flag = Flag.objects.create(name="flag_audit_probe")
    group = Group.objects.create(name="audit-probe-group")

    with enable_audit():
        flag.groups.add(group)

    assert _audited_deltas(flag, "groups"), "no audit event recorded for groups"
