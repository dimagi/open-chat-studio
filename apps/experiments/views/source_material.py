from django.contrib import messages
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import SourceMaterial
from apps.experiments.tables import SourceMaterialTable
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.web.waf import WafRule, waf_allow


class SourceMaterialHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        team_slug = self.kwargs["team_slug"]
        return {
            "active_tab": "source_material",
            "title": "Source Material",
            "new_object_url": reverse("experiments:source_material_new", args=[team_slug]),
            "table_url": reverse("experiments:source_material_table", args=[team_slug]),
            "enable_search": True,
        }


class SourceMaterialTableView(SingleTableView):
    model = SourceMaterial
    table_class = SourceMaterialTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        query_set = SourceMaterial.objects.filter(team=self.request.team, is_version=False)
        search = self.request.GET.get("search")
        if search:
            search_vector = SearchVector("topic", weight="A") + SearchVector("description", weight="B")
            search_query = SearchQuery(search)
            query_set = (
                query_set.annotate(document=search_vector, rank=SearchRank(search_vector, search_query))
                .filter(Q(document=search_query))
                .order_by("-rank")
            )
        return query_set


@waf_allow(WafRule.SizeRestrictions_BODY)
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
        "page_title": "Create Source Material",
        "button_text": "Create",
        "active_tab": "source_material",
    }

    def get_success_url(self):
        return reverse("experiments:source_material_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


@waf_allow(WafRule.SizeRestrictions_BODY)
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
        "page_title": "Update Source Material",
        "button_text": "Update",
        "active_tab": "source_material",
    }

    def get_queryset(self):
        return SourceMaterial.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:source_material_home", args=[self.request.team.slug])


class DeleteSourceMaterial(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        source_material = get_object_or_404(SourceMaterial, id=pk, team=request.team)
        source_material.archive()
        messages.success(request, "Source Material deleted")
        return HttpResponse()
