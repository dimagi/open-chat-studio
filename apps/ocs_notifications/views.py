from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.ocs_notifications.models import NotificationReceipt
from apps.ocs_notifications.tables import NotificationReceiptTable


class NotificationHome(LoginRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "notifications",
            "title": "Notifications",
            "table_url": reverse("ocs_notifications:notifications_table"),
            "enable_search": False,
        }


class NotificationReceiptTableView(LoginRequiredMixin, SingleTableView):
    model = NotificationReceipt
    table_class = NotificationReceiptTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return NotificationReceipt.objects.filter(user=self.request.user).select_related("notification")
