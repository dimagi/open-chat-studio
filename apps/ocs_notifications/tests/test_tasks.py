import datetime

import pytest
from django.utils import timezone
from time_machine import travel

from apps.ocs_notifications.models import EventType, EventUser, NotificationEvent
from apps.ocs_notifications.tasks import cleanup_old_notification_events
from apps.utils.factories.notifications import EventTypeFactory, EventUserFactory, NotificationEventFactory


@pytest.mark.django_db()
class TestCleanupOldNotificationEvents:
    def test_deletes_event_types_older_than_3_months(self, team):
        old_date = timezone.now() - datetime.timedelta(days=91)
        with travel(old_date, tick=False):
            old_event_type = EventTypeFactory.create(team=team)

        cleanup_old_notification_events()

        assert not EventType.objects.filter(pk=old_event_type.pk).exists()

    def test_keeps_event_types_within_3_months(self, team):
        recent_event_type = EventTypeFactory.create(team=team)

        cleanup_old_notification_events()

        assert EventType.objects.filter(pk=recent_event_type.pk).exists()

    def test_cascades_to_notification_events_and_event_users(self, team, django_user_model):
        old_date = timezone.now() - datetime.timedelta(days=91)
        with travel(old_date, tick=False):
            old_event_type = EventTypeFactory.create(team=team)
            notification_event = NotificationEventFactory.create(team=team, event_type=old_event_type)
            user = django_user_model.objects.create_user(username="testuser", password="password")
            event_user = EventUserFactory.create(team=team, event_type=old_event_type, user=user)

        cleanup_old_notification_events()

        assert not EventType.objects.filter(pk=old_event_type.pk).exists()
        assert not NotificationEvent.objects.filter(pk=notification_event.pk).exists()
        assert not EventUser.objects.filter(pk=event_user.pk).exists()

    def test_only_deletes_event_types_older_than_exactly_90_days(self, team):
        now = timezone.now()
        boundary_date = now - datetime.timedelta(days=90)
        with travel(boundary_date, tick=False):
            boundary_event_type = EventTypeFactory.create(team=team)

        with travel(now, tick=False):
            cleanup_old_notification_events()

        assert EventType.objects.filter(pk=boundary_event_type.pk).exists()

    def test_no_op_when_no_old_events(self, team):
        EventTypeFactory.create(team=team)

        cleanup_old_notification_events()

        assert EventType.objects.filter(team=team).count() == 1
