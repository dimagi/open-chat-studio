from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.ocs_notifications.models import UserNotification
from apps.ocs_notifications.tables import UserNotificationTable


class NotificationHome(LoginRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "notifications",
            "title": "Notifications",
            "table_url": reverse("ocs_notifications:notifications_table"),
            "enable_search": False,
        }


class UserNotificationTableView(LoginRequiredMixin, SingleTableView):
    model = UserNotification
    table_class = UserNotificationTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return UserNotification.objects.filter(user=self.request.user).select_related("notification")


class ToggleNotificationReadView(LoginRequiredMixin, TemplateView):
    template_name = "ocs_notifications/components/read_button.html"

    def post(self, request, *args, **kwargs):
        notification_id = kwargs.get("notification_id")
        user_notification = get_object_or_404(UserNotification, id=notification_id, user=request.user)

        # Toggle the read status
        user_notification.read = not user_notification.read
        if user_notification.read:
            user_notification.read_at = timezone.now()
        else:
            user_notification.read_at = None
        user_notification.save()

        # Return the updated button
        return self.render_to_response({"record": user_notification})
