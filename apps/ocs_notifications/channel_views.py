from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.ocs_notifications.forms import NotificationChannelForm
from apps.ocs_notifications.models import NotificationChannel
from apps.ocs_notifications.tables import NotificationChannelTable
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Flag


class FlagRequiredMixin:
    flag_name = "flag_slack_notifications"

    def dispatch(self, request, *args, **kwargs):
        if not Flag.get(self.flag_name).is_active_for_team(request.team):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class NotificationChannelHome(LoginAndTeamRequiredMixin, FlagRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"
    permission_required = "ocs_notifications.view_notificationchannel"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "manage_team",
            "title": "Notification Channels",
            "new_object_url": reverse("ocs_notifications_channels:new", args=[self.kwargs["team_slug"]]),
            "table_url": reverse("ocs_notifications_channels:table", args=[self.kwargs["team_slug"]]),
        }


class NotificationChannelTableView(
    LoginAndTeamRequiredMixin, FlagRequiredMixin, PermissionRequiredMixin, SingleTableView
):  # ty: ignore[invalid-method-override]
    model = NotificationChannel
    table_class = NotificationChannelTable
    template_name = "table/single_table.html"
    permission_required = "ocs_notifications.view_notificationchannel"

    def get_queryset(self):
        return (
            NotificationChannel.objects.filter(team=self.request.team)
            .select_related("messaging_provider")
            .order_by("channel_name")
        )


class CreateNotificationChannel(LoginAndTeamRequiredMixin, FlagRequiredMixin, PermissionRequiredMixin, CreateView):
    model = NotificationChannel
    form_class = NotificationChannelForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Notification Channel",
        "button_text": "Create",
        "active_tab": "manage_team",
    }
    permission_required = "ocs_notifications.add_notificationchannel"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {
            "request": self.request,
        }

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        messages.success(self.request, "Notification channel created.")
        return super().form_valid(form)


class EditNotificationChannel(LoginAndTeamRequiredMixin, FlagRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = NotificationChannel
    form_class = NotificationChannelForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Notification Channel",
        "button_text": "Update",
        "active_tab": "manage_team",
    }
    permission_required = "ocs_notifications.change_notificationchannel"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {
            "request": self.request,
        }

    def get_queryset(self):
        return NotificationChannel.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])


class DeleteNotificationChannel(LoginAndTeamRequiredMixin, FlagRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "ocs_notifications.delete_notificationchannel"

    def delete(self, request, team_slug: str, pk: int):
        notification_channel = get_object_or_404(NotificationChannel, id=pk, team=request.team)
        notification_channel.delete()
        messages.success(request, "Notification channel deleted.")
        return HttpResponse()
