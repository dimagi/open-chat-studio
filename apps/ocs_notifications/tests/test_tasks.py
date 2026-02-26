import datetime

import pytest
from django.utils import timezone
from time_machine import travel

from apps.ocs_notifications.models import NotificationEvent
from apps.ocs_notifications.tasks import cleanup_old_notification_events
from apps.utils.factories.notifications import EventTypeFactory, NotificationEventFactory


@pytest.mark.django_db()
class TestCleanupOldNotificationEvents:
    def test_deletes_notification_events_older_than_3_months(self, team):
        old_date = timezone.now() - datetime.timedelta(days=91)
        event_type = EventTypeFactory.create(team=team)
        with travel(old_date, tick=False):
            old_event = NotificationEventFactory.create(team=team, event_type=event_type)

        cleanup_old_notification_events()

        assert not NotificationEvent.objects.filter(pk=old_event.pk).exists()

    def test_keeps_notification_events_within_3_months(self, team):
        event_type = EventTypeFactory.create(team=team)
        recent_event = NotificationEventFactory.create(team=team, event_type=event_type)

        cleanup_old_notification_events()

        assert NotificationEvent.objects.filter(pk=recent_event.pk).exists()

    def test_only_deletes_notification_events_older_than_exactly_90_days(self, team):
        now = timezone.now()
        boundary_date = now - datetime.timedelta(days=90)
        event_type = EventTypeFactory.create(team=team)
        with travel(boundary_date, tick=False):
            boundary_event = NotificationEventFactory.create(team=team, event_type=event_type)

        with travel(now, tick=False):
            cleanup_old_notification_events()

        assert NotificationEvent.objects.filter(pk=boundary_event.pk).exists()

    def test_no_op_when_no_old_events(self, team):
        event_type = EventTypeFactory.create(team=team)
        NotificationEventFactory.create(team=team, event_type=event_type)

        cleanup_old_notification_events()

        assert NotificationEvent.objects.filter(team=team).count() == 1
