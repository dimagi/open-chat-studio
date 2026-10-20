"""Milestone notices for retired HTTP surfaces registered in `apps.utils.sunset`."""

from datetime import datetime
from itertools import groupby

from django.db.models import Min

from apps.ocs_notifications.models import EventUser
from apps.ocs_notifications.notifications import http_sunset_notification
from apps.ocs_notifications.utils import create_identifier, get_users_to_be_notified
from apps.utils.sunset import FIRST_DETECTION, SUNSETS, HttpSunset, due_milestone, is_enforced


def notify_sunsets(now: datetime) -> None:
    for spec in SUNSETS.values():
        experiments = spec.affected_experiments().select_related("team").order_by("team_id", "name")
        for team, team_experiments in groupby(experiments, key=lambda experiment: experiment.team):
            _notify_team(spec, team, list(team_experiments), now)


def _notify_team(spec: HttpSunset, team, experiments: list, now: datetime) -> None:
    if is_enforced(spec, team):
        return
    if not get_users_to_be_notified(team, list(spec.audience_permissions)):
        return
    first_detected_at = _first_detected_at(spec, team)
    milestone = FIRST_DETECTION if first_detected_at is None else due_milestone(spec, first_detected_at, now)
    if milestone is None:
        return
    http_sunset_notification(spec, team, experiments, milestone)


def _first_detected_at(spec: HttpSunset, team) -> datetime | None:
    identifier = create_identifier(spec.notification_slug, {"sunset": spec.key, "milestone": FIRST_DETECTION})
    return EventUser.objects.filter(team=team, event_type__identifier=identifier).aggregate(first=Min("created_at"))[
        "first"
    ]
