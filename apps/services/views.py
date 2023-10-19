from django import views
from django.http import HttpResponseRedirect
from django.shortcuts import render, resolve_url
from django_tables2 import SingleTableView

from apps.services.models import ServiceConfig, ServiceType
from apps.services.tables import ServiceConfigTable
from apps.services.utils import get_service_forms


class BaseCreateServiceConfigView(views.View):
    """This view should be used as a base view for creating a new service config of
    a specific service type"""

    service_type = None
    extra_context = None
    title = None

    def get(self, request, team_slug: str):
        return render(request, "generic/combined_object_form.html", self.get_context_data())

    def post(self, request, team_slug: str):
        combined_form = self.get_combined_form()
        if combined_form.is_valid():
            instance = combined_form.save()
            instance.team = request.team
            instance.save()
            return HttpResponseRedirect(self.get_success_url())
        return render(request, "generic/combined_object_form.html", self.get_context_data())

    def get_success_url(self):
        raise NotImplementedError

    def get_service_type(self):
        return ServiceType(self.service_type)

    def get_context_data(self):
        extra_context = self.extra_context or {}
        return {
            "title": self.title,
            "combined_form": self.get_combined_form(),
            "button_text": "Create",
            **extra_context,
        }

    def get_combined_form(self):
        data = None
        if self.request.method == "POST":
            data = self.request.POST
        return get_service_forms(self.get_service_type(), data=data)


class ConsentFormTableView(SingleTableView):
    model = ServiceType
    paginate_by = 25
    table_class = ServiceConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        query = ServiceConfig.objects.filter(team=self.request.team)
        if self.kwargs.get("subtype"):
            query = query.filter(subtype=self.kwargs["subtype"])
        return query


class CreateLlmProvider(BaseCreateServiceConfigView):
    service_type = ServiceType.LLM_PROVIDER
    extra_context = {
        "active_tab": "manage-team",
    }
    title = "Create LLM Provider"

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
