import pytest

from apps.ocs_notifications.models import NotificationEvent
from apps.ocs_notifications.notifications import survey_deprecation_notification
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_survey_deprecation_notification_creates_event():
    team = TeamFactory.create()
    survey_deprecation_notification(team)
    event = NotificationEvent.objects.filter(team=team).first()
    assert event is not None
    assert "2026-07-10" in event.message
    assert "deprecated" in event.message.lower()
