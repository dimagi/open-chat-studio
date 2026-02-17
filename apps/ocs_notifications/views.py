from django.contrib import messages
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
from apps.ocs_notifications.utils import TIMEDELTA_MAP, mute_notification, toggle_notification_read, unmute_notification
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.tables import render_table_row
from apps.web.dynamic_filters.datastructures import FilterParams


class NotificationHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notifications_table", args=[self.request.team.slug])
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
            "actions": [
                actions.Action(
                    url_name="ocs_notifications:toggle_do_not_disturb",
                    url_factory=lambda url_name, _request, _record, _value: reverse(
                        url_name, args=[_request.team.slug]
                    ),
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
        columns = UserNotificationFilter.columns(request=self.request, team=self.request.team)
        filter_context = get_filter_context_data(
            team=self.request.team,
            columns=columns,
            date_range_column="notification_date",
            table_url=table_url,
            table_container_id="data-table",
            table_type=FilterSet.TableType.NOTIFICATIONS,
        )
        context.update(filter_context)

        return context


class UserNotificationTableView(LoginAndTeamRequiredMixin, SingleTableView):
    model = EventUser
    table_class = UserNotificationTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        queryset = (
            EventUser.objects.with_latest_event()
            .with_mute_status()
            .filter(user=self.request.user, team=self.request.team)
            .select_related("event_type")
            .filter(last_event_at__isnull=False)
            .order_by("-last_event_at")
        )

        # Apply filters
        notification_filter = UserNotificationFilter()
        filter_params = FilterParams.from_request(self.request)
        user_timezone = self.request.session.get("detected_tz")

        return notification_filter.apply(queryset, filter_params=filter_params, timezone=user_timezone)


class ToggleNotificationReadView(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.with_latest_event().with_mute_status(),
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        toggle_notification_read(user=request.user, event_user=event_user, read=not event_user.read)

        return render_table_row(request, UserNotificationTable, event_user)


class MuteNotificationView(LoginAndTeamRequiredMixin, View):
    """Mute a specific notification identifier or all notifications"""

    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        event_user = get_object_or_404(
            EventUser.objects.select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
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
            team=request.team,
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


class UnmuteNotificationView(LoginAndTeamRequiredMixin, View):
    """Unmute a specific notification identifier or all notifications"""

    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            EventUser.objects.select_related("event_type"),
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        unmute_notification(user=request.user, team=request.team, event_type=user_notification.event_type)

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={"record": user_notification, "is_muted": False, "muted_until": None},
        )


class ToggleDoNotDisturbView(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str, *args, **kwargs):
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


class NotificationEventHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        event_type = get_object_or_404(EventType, team=self.request.team, id=self.kwargs["event_type_id"])
        table_url = reverse("ocs_notifications:notification_event_table", args=[self.request.team.slug, event_type.id])

        title = event_type.notificationevent_set.order_by("-created_at").values_list("title", flat=True).first()
        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "subtitle": title or "",
            "table_url": table_url,
            "enable_search": False,
        }

        return context


class NotificationEventTableView(LoginAndTeamRequiredMixin, SingleTableView):
    model = NotificationEvent
    table_class = NotificationEventTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            NotificationEvent.objects.filter(team=self.request.team, event_type_id=self.kwargs["event_type_id"])
            .select_related("event_type")
            .order_by("-created_at")
        )
