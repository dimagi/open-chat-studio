from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import NoActivityMessageConfig
from apps.experiments.tables import NoActivityMessageConfigTable
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def no_activity_config_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "no_activity_config",
            "title": "No Activity Config",
            "new_object_url": reverse("experiments:no_activity_new", args=[team_slug]),
            "table_url": reverse("experiments:no_activity_table", args=[team_slug]),
        },
    )


class NoActivityMessageConfigTableView(SingleTableView):
    model = NoActivityMessageConfig
    paginate_by = 25
    table_class = NoActivityMessageConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return NoActivityMessageConfig.objects.filter(team=self.request.team)


class CreateNoActivityMessageConfig(CreateView):
    model = NoActivityMessageConfig
    fields = [
        "name",
        "message_for_bot",
        "max_pings",
        "ping_after",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create No Activity Config",
        "button_text": "Create",
        "active_tab": "no_activity_config",
    }

    def get_success_url(self):
        return reverse("experiments:no_activity_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditNoActivityMessageConfig(UpdateView):
    model = NoActivityMessageConfig
    fields = [
        "name",
        "message_for_bot",
        "max_pings",
        "ping_after",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update No Activity Config",
        "button_text": "Update",
        "active_tab": "no_activity_config",
    }

    def get_queryset(self):
        return NoActivityMessageConfig.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:no_activity_home", args=[self.request.team.slug])


@login_and_team_required
def delete_no_activity_config(request, team_slug: str, pk: int):
    no_activity_config = get_object_or_404(NoActivityMessageConfig, id=pk, team=request.team)
    no_activity_config.delete()
    return HttpResponse()
