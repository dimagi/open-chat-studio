import json

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.annotations.forms import TagForm
from apps.annotations.models import Tag
from apps.annotations.tables import TagTable
from apps.teams.mixins import LoginAndTeamRequiredMixin


class TagHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "tags",
            "title": "Tags",
            "new_object_url": reverse("annotations:tag_new", args=[team_slug]),
            "table_url": reverse("annotations:tag_table", args=[team_slug]),
        }


class CreateTag(CreateView):
    model = Tag
    form_class = TagForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Tag",
        "button_text": "Create",
        "active_tab": "tags",
    }

    def get_success_url(self):
        return reverse("annotations:tag_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditTag(UpdateView):
    model = Tag
    form_class = TagForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Tag",
        "button_text": "Update",
        "active_tab": "tags",
    }

    def get_queryset(self):
        return Tag.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("annotations:tag_home", args=[self.request.team.slug])


class DeleteTag(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        tag = get_object_or_404(Tag, id=pk, team=request.team)
        tag.delete()
        messages.success(request, "Tag Deleted")
        return HttpResponse()


class TagTableView(SingleTableView):
    model = Tag
    paginate_by = 25
    table_class = TagTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Tag.objects.filter(team=self.request.team)


class UnlinkTag(LoginAndTeamRequiredMixin, View):
    # TODO: Update to accept a model content type to allow for generic models
    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        tag_name = request.POST["tag_name"]
        content_type = ContentType.objects.get(app_label=object_info["app"], model=object_info["model_name"])
        obj = content_type.get_object_for_this_type(id=object_id)
        obj.tags.remove(tag_name)
        return HttpResponse()


class LinkTag(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        tag_name = request.POST["tag_name"]
        content_type = ContentType.objects.get(app_label=object_info["app"], model=object_info["model_name"])
        obj = content_type.get_object_for_this_type(id=object_id)
        obj.add_tags(tag_name, team=request.team, added_by=request.user)
        return HttpResponse()
