from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http.response import HttpResponse as HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View
from django_tables2 import SingleTableView

from apps.experiments.filters import get_filter_context_data
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.ocs_notifications.filters import UserNotificationFilter
from apps.ocs_notifications.models import EventType, EventUser, NotificationEvent, UserNotificationPreferences
from apps.ocs_notifications.tables import NotificationEventTable, UserNotificationTable
from apps.ocs_notifications.utils import (
    TIMEDELTA_MAP,
    bust_unread_notification_cache,
    mute_notification,
    toggle_notification_read,
    unmute_notification,
)
from apps.utils.tables import render_table_row
from apps.web.dynamic_filters.datastructures import FilterParams


class NotificationHome(LoginRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notifications_table")
        user_preferences, _created = UserNotificationPreferences.objects.get_or_create(
            user=self.request.user, team=self.request.team
        )
        do_not_disturbed_active = bool(user_preferences.do_not_disturb_until)
        end_datetime = None
        if user_preferences.do_not_disturb_until and user_preferences.do_not_disturb_until < timezone.now():
            user_preferences.do_not_disturb_until = None
            user_preferences.save(update_fields=["do_not_disturb_until"])
            do_not_disturbed_active = False
        elif user_preferences.do_not_disturb_until:
            end_datetime = user_preferences.do_not_disturb_until

        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "table_url": table_url,
            "enable_search": False,
            "filter_bar_action": "ocs_notifications/components/mark_all_read_button.html",
            "mark_all_read_url": reverse("ocs_notifications:mark_all_notifications_read"),
            "actions": [
                actions.Action(
                    url_name="ocs_notifications:toggle_do_not_disturb",
                    url_factory=lambda url_name, _request, _record, _value: reverse(url_name),
                    template="ocs_notifications/components/do_not_disturb_button.html",
                    extra_context={"is_activated": do_not_disturbed_active, "end_datetime": end_datetime},
                ),
                actions.Action(
                    url_name="users:user_profile",
                    url_factory=lambda url_name, _request, _record, _value: reverse(url_name),
                    label="Preferences",
                    icon_class="fa fa-cog",
                ),
            ],
        }

        # Add filter context
        columns = UserNotificationFilter.columns(team=self.request.team, user=self.request.user)
        filter_context = get_filter_context_data(
            team=self.request.team,
            columns=columns,
            filter_class=UserNotificationFilter,
            table_url=table_url,
            table_container_id="data-table",
            table_type=FilterSet.TableType.NOTIFICATIONS,
        )
        context.update(filter_context)

        return context


def get_filtered_user_notifications(request):
    """Build the current user's cross-team notifications queryset, narrowed by whatever
    filters (including the team filter) are active in the requesting view.

    Shared by ``UserNotificationTableView`` and ``MarkAllNotificationsReadView`` so that
    "mark all read" always acts on exactly the rows currently displayed in the table.
    """
    queryset = (
        EventUser.objects.with_latest_event()
        .with_mute_status()
        .filter(user=request.user, team__in=request.user.teams.all())
        .select_related("event_type", "team")
        .filter(latest_event_created_at__isnull=False)
    )

    notification_filter = UserNotificationFilter()
    filter_params = FilterParams.from_request(request)
    user_timezone = request.session.get("detected_tz")

    return notification_filter.apply(queryset, filter_params=filter_params, timezone=user_timezone)


class UserNotificationTableView(LoginRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = EventUser
    table_class = UserNotificationTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return get_filtered_user_notifications(self.request).order_by("-latest_event_created_at")


class ToggleNotificationReadView(LoginRequiredMixin, View):
    def post(self, request, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.with_latest_event().with_mute_status(),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        toggle_notification_read(user=request.user, event_user=event_user, read=not event_user.read)

        return render_table_row(request, UserNotificationTable, event_user)


class MuteNotificationView(LoginRequiredMixin, View):
    """Mute a specific notification identifier or all notifications"""

    def post(self, request, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.with_mute_status().select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        # Get duration from POST data (in hours)
        duration_param = request.POST.get("duration")
        if duration_param not in TIMEDELTA_MAP:
            messages.error(request, "Invalid duration for muting notifications.")
            return render(
                request,
                "ocs_notifications/components/mute_button.html",
                context={
                    "record": event_user,
                    "is_muted": event_user.is_muted,
                    "muted_until": event_user.muted_until,
                },
            )
        event_user = mute_notification(
            user=request.user,
            team=event_user.team,
            event_type=event_user.event_type,
            timedelta=TIMEDELTA_MAP[duration_param],
        )

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={
                "record": event_user,
                "is_muted": True,
                "muted_until": event_user.muted_until,
            },
        )


class UnmuteNotificationView(LoginRequiredMixin, View):
    """Unmute a specific notification identifier or all notifications"""

    def post(self, request, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            EventUser.objects.select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__in=request.user.teams.all(),
        )

        unmute_notification(user=request.user, team=user_notification.team, event_type=user_notification.event_type)

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={"record": user_notification, "is_muted": False, "muted_until": None},
        )


class ToggleDoNotDisturbView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        duration_param = request.POST.get("duration", None)
        user_preferences, _created = UserNotificationPreferences.objects.get_or_create(
            user=request.user, team=request.team
        )

        update = True
        if duration_param == "":
            # Reset do not disturb
            user_preferences.do_not_disturb_until = None
        elif duration_param in TIMEDELTA_MAP and duration_param != "forever":
            timedelta = TIMEDELTA_MAP.get(duration_param)
            user_preferences.do_not_disturb_until = timezone.now() + timedelta.value
        else:
            update = False
            messages.error(request, "Invalid duration for Do Not Disturb")

        if update:
            user_preferences.save(update_fields=["do_not_disturb_until"])

        return render(
            request,
            "ocs_notifications/components/do_not_disturb_button.html",
            context={"end_datetime": user_preferences.do_not_disturb_until},
        )


class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        # Same filters (including the team filter) as the table currently displayed, so "mark
        # all read" only ever touches the filtered set the user is looking at.
        unread = get_filtered_user_notifications(request).filter(read=False)
        team_slugs = list(unread.values_list("team__slug", flat=True).distinct())
        unread.update(read=True, read_at=timezone.now())

        for team_slug in team_slugs:
            bust_unread_notification_cache(user_id=request.user.id, team_slug=team_slug)

        table_url = reverse("ocs_notifications:notifications_table")
        return render(request, "ocs_notifications/components/mark_all_read_reload.html", {"table_url": table_url})


class NotificationEventHome(LoginRequiredMixin, TemplateView):
    template_name = "ocs_notifications/notification_event_home.html"

    @cached_property
    def event_type(self) -> EventType:
        return get_object_or_404(
            EventType.objects.select_related("team"),
            team__in=self.request.user.teams.all(),
            id=self.kwargs["event_type_id"],
        )

    def get(self, request, *args, **kwargs) -> HttpResponse:
        # Clicking the event marks it as read. We explicitly don't use the toggle_notification_read function here to
        # avoid multiple DB queries
        EventUser.objects.filter(
            user=self.request.user, team=self.event_type.team, event_type=self.event_type, read=False
        ).update(read=True, read_at=timezone.now())
        bust_unread_notification_cache(user_id=self.request.user.id, team_slug=self.event_type.team.slug)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notification_event_table", args=[self.event_type.id])

        title = self.event_type.notificationevent_set.order_by("-created_at").values_list("title", flat=True).first()
        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "subtitle": title or "",
            "table_url": table_url,
            "enable_search": False,
        }

        return context


class NotificationEventTableView(LoginRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = NotificationEvent
    table_class = NotificationEventTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            NotificationEvent.objects.filter(
                team__in=self.request.user.teams.all(), event_type_id=self.kwargs["event_type_id"]
            )
            .select_related("event_type")
            .order_by("-created_at")
        )
