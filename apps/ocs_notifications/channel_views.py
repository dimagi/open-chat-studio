from functools import wraps

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.ocs_notifications.forms import NotificationChannelForm
from apps.ocs_notifications.models import NotificationChannel
from apps.ocs_notifications.tables import NotificationChannelTable
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.utils import flag_is_active_for_team


def slack_notifications_flag_required(view_func):
    """404 when the team has not enabled the Slack notifications feature flag."""

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not flag_is_active_for_team(request.team, Flags.SLACK_NOTIFICATIONS.slug):
            raise Http404
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def slack_notifications_flag_decorator():
    return method_decorator(slack_notifications_flag_required, name="dispatch")


@slack_notifications_flag_decorator()
class NotificationChannelHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"
    permission_required = "ocs_notifications.view_notificationchannel"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "manage_team",
            "title": "Notification Channels",
            "new_object_url": reverse("ocs_notifications_channels:new", args=[self.kwargs["team_slug"]]),
            "table_url": reverse("ocs_notifications_channels:table", args=[self.kwargs["team_slug"]]),
        }


@slack_notifications_flag_decorator()
class NotificationChannelTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
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


@slack_notifications_flag_decorator()
class CreateNotificationChannel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
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


@slack_notifications_flag_decorator()
class EditNotificationChannel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
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


@slack_notifications_flag_decorator()
class DeleteNotificationChannel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "ocs_notifications.delete_notificationchannel"

    def delete(self, request, team_slug: str, pk: int):
        notification_channel = get_object_or_404(NotificationChannel, id=pk, team=request.team)
        notification_channel.delete()
        messages.success(request, "Notification channel deleted.")
        return HttpResponse()
