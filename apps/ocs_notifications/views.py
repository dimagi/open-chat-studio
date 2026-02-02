from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.experiments.filters import get_filter_context_data
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.ocs_notifications.filters import UserNotificationFilter
from apps.ocs_notifications.models import UserNotification
from apps.ocs_notifications.tables import UserNotificationTable
from apps.web.dynamic_filters.datastructures import FilterParams

from .utils import bust_unread_notification_cache


class NotificationHome(LoginRequiredMixin, TemplateView):
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


class UserNotificationTableView(LoginRequiredMixin, SingleTableView):
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


class ToggleNotificationReadView(LoginRequiredMixin, SingleTableView):
    model = UserNotification
    table_class = UserNotificationTable
    template_name = "table/single_table.html"

    def post(self, request, team_slug: str, *args, **kwargs):
        notification_id = kwargs.get("notification_id")
        user_notification = get_object_or_404(
            UserNotification, id=notification_id, user=request.user, team__slug=team_slug
        )

        # Toggle the read status
        user_notification.read = not user_notification.read
        if user_notification.read:
            user_notification.read_at = timezone.now()
        else:
            user_notification.read_at = None
        user_notification.save()
        bust_unread_notification_cache(request.user.id, team_slug=team_slug)

        # Return the updated filtered table
        return self.get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = UserNotification.objects.filter(
            user=self.request.user, team__slug=self.kwargs["team_slug"]
        ).select_related("notification")

        # Apply filters
        notification_filter = UserNotificationFilter()
        filter_params = FilterParams.from_request(self.request)
        user_timezone = self.request.session.get("detected_tz")

        return notification_filter.apply(queryset, filter_params=filter_params, timezone=user_timezone)
