import json
import unicodedata

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.annotations.forms import TagForm
from apps.annotations.models import CustomTaggedItem, Tag, TagCategories
from apps.annotations.tables import TagTable
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search


class TagHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "annotations.view_tag"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "tags",
            "title": "Tags",
            "new_object_url": reverse("annotations:tag_new", args=[team_slug]),
            "table_url": reverse("annotations:tag_table", args=[team_slug]),
            "enable_search": True,
        }


class CreateTag(CreateView, PermissionRequiredMixin):
    permission_required = "annotations.add_tag"
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
        form.instance.name = unicodedata.normalize("NFC", form.instance.name)

        try:
            with transaction.atomic():
                return super().form_valid(form)
        except IntegrityError:
            form.add_error("name", "A tag with this name already exists for this team, system status, and category.")
        return self.form_invalid(form)


class EditTag(UpdateView, PermissionRequiredMixin):
    permission_required = "annotations.change_tag"
    model = Tag
    form_class = TagForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Tag",
        "button_text": "Update",
        "active_tab": "tags",
    }

    def get_queryset(self):
        return Tag.objects.filter(team=self.request.team, is_system_tag=False)

    def get_success_url(self):
        return reverse("annotations:tag_home", args=[self.request.team.slug])

    def get(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        queryset = self.get_queryset()
        if not queryset.filter(pk=pk).exists():
            return redirect("annotations:tag_home", team_slug=self.request.team.slug)
        return super().get(request, *args, **kwargs)


class DeleteTag(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.delete_tag"

    def delete(self, request, team_slug: str, pk: int):
        tag = get_object_or_404(Tag, id=pk, team=request.team)
        if tag.is_system_tag:
            return HttpResponseForbidden("System tags cannot be deleted.")
        tag.delete()
        messages.success(request, "Tag Deleted")
        return HttpResponse()


class TagTableView(SingleTableView):
    model = Tag
    table_class = TagTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        queryset = Tag.objects.filter(team=self.request.team)

        search = self.request.GET.get("search")
        if search:
            queryset = similarity_search(
                queryset,
                search_phase=search,
                columns=["name"],
                extra_conditions=Q(name__icontains=search),
                score=0.1,
            )

        return queryset


class UnlinkTag(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.delete_customtaggeditem"

    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        tag_name = request.POST["tag_name"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        obj = content_type.get_object_for_this_type(id=object_id)
        obj.tags.remove(tag_name)
        return HttpResponse()


class TagUI(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.view_customtaggeditem"

    def get(self, request, team_slug: str):
        object_id = request.GET.get("id")
        content_type = get_object_or_404(
            ContentType, app_label=request.GET.get("app"), model=request.GET.get("model_name")
        )
        model_class = content_type.model_class()
        obj = model_class.objects.prefetch_related(
            Prefetch(
                "tagged_items",
                queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                to_attr="prefetched_tagged_items",
            )
        ).get(id=object_id)

        return render(
            request,
            "annotations/tag_ui.html",
            {
                "team_slug": team_slug,
                "object": obj,
                "edit_mode": request.GET.get("edit"),
                "available_tags": [
                    t.name
                    for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False)
                    .exclude(category=TagCategories.RESPONSE_RATING)
                    .all()
                ],
            },
        )


class LinkTag(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.add_customtaggeditem"

    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        tag_name = request.POST["tag_name"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        obj = content_type.get_object_for_this_type(id=object_id)
        tag_exists = Tag.objects.filter(name=tag_name, team__slug=team_slug).exists()

        if not tag_exists and request.user.has_perm("annotations.add_tag"):
            obj.tags.create(team=request.team, name=tag_name, created_by=request.user)

        obj.add_tags([tag_name], team=request.team, added_by=request.user)
        return HttpResponse()
