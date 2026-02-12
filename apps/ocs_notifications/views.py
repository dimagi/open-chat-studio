from django.db.models import Exists, F, OuterRef, Subquery
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View
from django_tables2 import SingleTableView

from apps.experiments.filters import get_filter_context_data
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.ocs_notifications.filters import UserNotificationFilter
from apps.ocs_notifications.models import NotificationMute, UserNotification, UserNotificationPreferences
from apps.ocs_notifications.tables import UserNotificationTable
from apps.ocs_notifications.utils import mute_notification, toggle_notification_read, unmute_notification
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.tables import render_table_row
from apps.web.dynamic_filters.datastructures import FilterParams

# Duration constants (in hours)
DURATION_8H = 8
DURATION_1D = 24
DURATION_1W = 168  # 7 * 24
DURATION_1M = 720  # 30 * 24

# Map duration parameter values to hours
DURATION_MAP = {
    "8h": DURATION_8H,
    "1d": DURATION_1D,
    "1w": DURATION_1W,
    "1m": DURATION_1M,
    "forever": None,  # Special case for muting indefinitely
}


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
    model = UserNotification
    table_class = UserNotificationTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        subquery = NotificationMute.objects.filter(
            user_id=self.request.user.id,
            team_id=self.request.team.id,
            notification_identifier=OuterRef("notification__identifier"),
            muted_until__gt=timezone.now(),
        )

        queryset = (
            UserNotification.objects.filter(user=self.request.user, team=self.request.team)
            .annotate(notification_is_muted=Exists(subquery), muted_until=Subquery(subquery.values("muted_until")[:1]))
            .select_related("notification")
            .order_by("-notification__last_event_at")
        )

        # Apply filters
        notification_filter = UserNotificationFilter()
        filter_params = FilterParams.from_request(self.request)
        user_timezone = self.request.session.get("detected_tz")

        return notification_filter.apply(queryset, filter_params=filter_params, timezone=user_timezone)


class ToggleNotificationReadView(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            UserNotification,
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        toggle_notification_read(
            user=request.user, user_notification=user_notification, read=not user_notification.read
        )

        # Return the updated filtered table
        return render_table_row(request, UserNotificationTable, user_notification)


class MuteNotificationView(LoginAndTeamRequiredMixin, View):
    """Mute a specific notification identifier or all notifications"""

    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            UserNotification.objects.annotate(identifier=F("notification__identifier")),
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        # Get duration from POST data (in hours)
        duration_param = request.POST.get("duration")
        duration_hours = DURATION_MAP.get(duration_param)

        notification_mute = mute_notification(
            user=request.user,
            team=request.team,
            notification_identifier=user_notification.identifier,
            duration_hours=duration_hours,
        )

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={
                "record": user_notification,
                "notification_is_muted": True,
                "muted_until": notification_mute.muted_until,
            },
        )


class UnmuteNotificationView(LoginAndTeamRequiredMixin, View):
    """Unmute a specific notification identifier or all notifications"""

    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            UserNotification.objects.annotate(identifier=F("notification__identifier")),
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        unmute_notification(user=request.user, team=request.team, notification_identifier=user_notification.identifier)

        return render(
            request,
            "ocs_notifications/components/mute_button.html",
            context={"record": user_notification, "notification_is_muted": False, "muted_until": None},
        )


class ToggleDoNotDisturbView(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str, *args, **kwargs):
        duration_param = request.POST.get("duration", None)
        user_preferences, _created = UserNotificationPreferences.objects.get_or_create(
            user=request.user, team=request.team
        )
        until = None
        if duration_param:
            duration_hours = DURATION_MAP.get(duration_param)
            until = timezone.now() + timezone.timedelta(hours=duration_hours)

        user_preferences.do_not_disturb_until = until
        user_preferences.save(update_fields=["do_not_disturb_until"])

        return render(
            request,
            "ocs_notifications/components/do_not_disturb_button.html",
            context={"is_activated": bool(until), "end_datetime": user_preferences.do_not_disturb_until},
        )
