from django import views
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render, resolve_url
from django_tables2 import SingleTableView

from apps.services.models import ServiceConfig, ServiceType
from apps.services.tables import ServiceConfigTable
from apps.services.utils import get_service_forms


class BaseServiceConfigView(views.View):
    """This view should be used as a base view for creating a new service config of
    a specific service type"""

    service_type = None
    extra_context = None
    title = None

    _object = None

    def get(self, request, team_slug: str, pk: int = None):
        return render(request, "generic/combined_object_form.html", self.get_context_data())

    def post(self, request, team_slug: str, pk: int = None):
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
        form = self.get_combined_form()
        obj = self.get_object()
        return {
            "title": self.title,
            "combined_form": form,
            "secondary_key": form.get_secondary_key(obj),
            "button_text": "Update" if obj else "Create",
            **extra_context,
        }

    def get_combined_form(self):
        data = None
        if self.request.method == "POST":
            data = self.request.POST
        return get_service_forms(self.get_service_type(), data=data, instance=self.get_object())

    def get_object(self):
        if self.kwargs.get("pk") and not self._object:
            self._object = get_object_or_404(ServiceConfig, service_type=self.service_type, pk=self.kwargs["pk"])
        return self._object

    def get_title(self):
        obj = self.get_object()
        if obj:
            return f"Edit {self.get_object().name}"
        return self.title or f"Create {self.get_service_type().label}"


class ServiceConfigTableView(SingleTableView):
    paginate_by = 25
    table_class = ServiceConfigTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        query = ServiceConfig.objects.filter(team=self.request.team)
        if self.kwargs.get("subtype"):
            query = query.filter(subtype=self.kwargs["subtype"])
        return query


def delete_service_config(request, team_slug: str, pk: int):
    service_config = get_object_or_404(ServiceConfig, team=request.team, pk=pk)
    service_config.delete()
    return HttpResponse()


class CreateEditLlmProvider(BaseServiceConfigView):
    service_type = ServiceType.LLM_PROVIDER
    extra_context = {
        "active_tab": "manage-team",
    }

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
