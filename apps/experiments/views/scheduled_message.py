from django import forms
from django.forms.models import BaseModelForm
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import ScheduledMessage
from apps.experiments.tables import ScheduledMessageTable
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def scheduled_message_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "scheduled_messages",
            "title": "Scheduled Messages",
            "new_object_url": reverse("experiments:scheduled_message_new", args=[team_slug]),
            "table_url": reverse("experiments:scheduled_message_table", args=[team_slug]),
        },
    )


class ScheduledMessageTableView(SingleTableView):
    model = ScheduledMessage
    paginate_by = 25
    table_class = ScheduledMessageTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return ScheduledMessage.objects.filter(team_id=self.request.team)


class ScheduledMessageMixin:
    def get_form(self):
        form = super().get_form()
        form.fields["clocked_schedule"] = forms.DateTimeField(
            widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
        )
        return form


class CreateScheduledMessage(ScheduledMessageMixin, CreateView):
    model = ScheduledMessage
    fields = ["name", "message", "clocked_schedule", "is_bot_instruction", "chat_ids", "experiment"]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Scheduled Message",
        "button_text": "Create",
        "active_tab": "scheduled_message",
    }

    def get_success_url(self):
        return reverse("experiments:scheduled_message_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditScheduledMessage(ScheduledMessageMixin, UpdateView):
    model = ScheduledMessage
    fields = ["name", "message", "clocked_schedule", "is_bot_instruction", "chat_ids", "experiment"]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Scheduled Message",
        "button_text": "Update",
        "active_tab": "scheduled_message",
    }

    def get_queryset(self):
        return ScheduledMessage.objects.filter(team_id=self.request.team)

    def get_success_url(self):
        return reverse("experiments:scheduled_message_home", args=[self.request.team.slug])


@login_and_team_required
def delete_scheduled_message(request, team_slug: str, pk: int):
    scheduled_message = get_object_or_404(ScheduledMessage, id=pk, team_id=request.team.id)
    scheduled_message.delete()
    return HttpResponse()
