from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import SourceMaterial
from apps.experiments.tables import SourceMaterialTable
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def source_material_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "source_material",
            "title": "Source Material",
            "new_object_url": reverse("experiments:source_material_new", args=[team_slug]),
            "table_url": reverse("experiments:source_material_table", args=[team_slug]),
        },
    )


class SourceMaterialTableView(SingleTableView):
    model = SourceMaterial
    paginate_by = 25
    table_class = SourceMaterialTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return SourceMaterial.objects.filter(team=self.request.team)


class CreateSourceMaterial(CreateView):
    model = SourceMaterial
    fields = [
        "topic",
        "description",
        "material",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Source Material",
        "button_text": "Create",
        "active_tab": "source_material",
    }

    def get_success_url(self):
        return reverse("experiments:source_material_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditSourceMaterial(UpdateView):
    model = SourceMaterial
    fields = [
        "topic",
        "description",
        "material",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Source Material",
        "button_text": "Update",
        "active_tab": "source_material",
    }

    def get_queryset(self):
        return SourceMaterial.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:source_material_home", args=[self.request.team.slug])


@login_and_team_required
def delete_source_material(request, team_slug: str, pk: int):
    source_material = get_object_or_404(SourceMaterial, id=pk, team=request.team)
    source_material.delete()
    return HttpResponse()
