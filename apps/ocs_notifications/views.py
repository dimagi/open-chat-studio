from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView, View
from django_tables2 import SingleTableView

from apps.experiments.filters import get_filter_context_data
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.ocs_notifications.filters import UserNotificationFilter
from apps.ocs_notifications.models import UserNotification
from apps.ocs_notifications.tables import UserNotificationTable
from apps.ocs_notifications.utils import create_or_update_mute, delete_mute, toggle_notification_read
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
    "forever": None,
}

# Notification type constants
NOTIFICATION_TYPE_ALL = "all"
NOTIFICATION_TYPE_SPECIFIC = "specific"


class NotificationHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        table_url = reverse("ocs_notifications:notifications_table", args=[self.request.team.slug])
        context = {
            "active_tab": "notifications",
            "title": "Notifications",
            "table_url": table_url,
            "enable_search": False,
            "actions": [
                actions.Action(
                    url_name="users:user_profile",
                    url_factory=lambda url_name, _request, _record, _value: reverse(url_name),
                    label="Preferences",
                    icon_class="fa fa-cog",
                )
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
        queryset = UserNotification.objects.filter(user=self.request.user, team=self.request.team).select_related(
            "notification"
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
            UserNotification,
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        # Get the notification identifier
        notification_identifier = user_notification.notification.identifier

        # Get duration from POST data (in hours)
        duration_param = request.POST.get("duration")
        notification_type = request.POST.get("notification_type")  # 'specific' or 'all'

        duration_hours = DURATION_MAP.get(duration_param)

        # Determine what to mute
        mute_identifier = None if notification_type == NOTIFICATION_TYPE_ALL else notification_identifier

        create_or_update_mute(
            user=request.user, team=request.team, notification_identifier=mute_identifier, duration_hours=duration_hours
        )

        message = (
            f"Notifications muted for {duration_param}"
            if duration_param != "forever"
            else "Notifications muted permanently"
        )
        return JsonResponse({"success": True, "message": message})


class UnmuteNotificationView(LoginAndTeamRequiredMixin, View):
    """Unmute a specific notification identifier or all notifications"""

    def post(self, request, team_slug: str, notification_id: int, *args, **kwargs):
        user_notification = get_object_or_404(
            UserNotification,
            id=notification_id,
            user=self.request.user,
            team__slug=team_slug,
        )

        # Get the notification identifier
        notification_identifier = user_notification.notification.identifier
        notification_type = request.POST.get("notification_type")  # 'specific' or 'all'

        # Determine what to unmute
        mute_identifier = None if notification_type == NOTIFICATION_TYPE_ALL else notification_identifier

        delete_mute(user=request.user, team=request.team, notification_identifier=mute_identifier)

        return JsonResponse({"success": True, "message": "Notifications unmuted"})
