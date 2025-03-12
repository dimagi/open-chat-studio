import json
from functools import cache

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from apps.documents.models import Repository, RepositoryType
from apps.files.models import File
from apps.generics.chips import Chip
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class RepositoryHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "documents/repositories.html"

    def get_context_data(self, team_slug: str, tab_name: str, **kwargs):
        context = {
            "active_tab": "manage_files",
            "title": "Manage Files",
            "tab_name": tab_name,
            "files_list_url": reverse("documents:files_list", kwargs={"team_slug": team_slug}),
            "upload_files_url": reverse("documents:upload_files", kwargs={"team_slug": team_slug}),
            "collections_list_url": reverse("documents:collections_list", kwargs={"team_slug": team_slug}),
            "new_collection_url": reverse("documents:new_collection", kwargs={"team_slug": team_slug}),
            "files_count": File.objects.filter(team__slug=team_slug).count(),
            "collections_count": Repository.objects.filter(
                team__slug=team_slug, type=RepositoryType.COLLECTION
            ).count(),
        }
        if tab_name == "files":
            context["collections"] = Repository.objects.filter(
                team__slug=team_slug, type=RepositoryType.COLLECTION
            ).all()

        return context


class BaseObjectListView(ListView, PermissionRequiredMixin):
    details_url_name: str

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["details_url_name"] = self.details_url_name
        context["tab_name"] = self.tab_name
        return context


class BaseDetailsView(TemplateView, PermissionRequiredMixin):
    @cache
    def get_object(self):
        return self.model.objects.get(team__slug=self.kwargs["team_slug"], id=self.kwargs["id"])

    def get_context_data(self, team_slug: str, id, **kwargs):
        return {"object": self.get_object()}


class FileListView(LoginAndTeamRequiredMixin, BaseObjectListView):
    template_name = "documents/shared/list.html"
    model = File
    paginate_by = 10
    details_url_name = "documents:file_details"
    tab_name = "files"
    permission_required = "files.view_file"

    def get_queryset(self):
        queryset = super().get_queryset().filter(team__slug=self.kwargs["team_slug"]).order_by("-created_at")
        search = self.request.GET.get("search")
        if search:
            # TODO: Expand to search summary as well
            name_similarity = TrigramSimilarity("name", search)
            queryset = (
                queryset.annotate(similarity=name_similarity).filter(Q(similarity__gt=0.2)).order_by("-similarity")
            )
        return queryset


class FileDetails(LoginAndTeamRequiredMixin, BaseDetailsView):
    template_name = "documents/file_details.html"
    model = File
    permission_required = "files.view_file"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        file = self.get_object()
        collection_names = file.repository_set.filter(type=RepositoryType.COLLECTION).values_list("name", flat=True)
        context["current_collections"] = list(collection_names)
        context["edit_url"] = reverse(
            "documents:edit_file", kwargs={"team_slug": self.kwargs["team_slug"], "id": file.id}
        )
        context["delete_url"] = reverse(
            "documents:delete_file", kwargs={"team_slug": self.kwargs["team_slug"], "id": file.id}
        )
        context["available_collections"] = Repository.objects.filter(
            team__slug=self.kwargs["team_slug"], type=RepositoryType.COLLECTION
        ).values_list("name", flat=True)
        return context


@require_POST
@transaction.atomic()
@permission_required("files.add_file")
def upload_files(request, team_slug: str):
    """Upload files to a collection"""
    # TODO: Check collection size and error if it's too large with the new files added
    files = []
    file_summaries = json.loads(request.POST["file_summaries"])
    for uploaded_file in request.FILES.getlist("files"):
        files.append(
            File.objects.create(
                team=request.team,
                name=uploaded_file.name,
                file=uploaded_file,
                summary=file_summaries[uploaded_file.name],
            )
        )
    repo, _ = Repository.objects.get_or_create(
        type=RepositoryType.COLLECTION, team=request.team, name=request.POST.get("collection_name")
    )
    repo.files.add(*files)
    return redirect(reverse("documents:repositories", kwargs={"team_slug": team_slug, "tab_name": "files"}))


@login_and_team_required
@permission_required("files.delete_file")
def delete_file(request, team_slug: str, id: int):
    file = get_object_or_404(File, team__slug=team_slug, id=id)
    file.delete()
    messages.success(request, "File deleted")
    return HttpResponse()


@login_and_team_required
@require_POST
@permission_required("files.change_file")
def edit_file(request, team_slug: str, id: int):
    file = get_object_or_404(File.objects.defer("file"), team__slug=team_slug, id=id)
    file.name = request.POST.get("name")
    file.summary = request.POST.get("summary")

    existing_collections = set(
        file.repository_set.filter(type=RepositoryType.COLLECTION).values_list("name", flat=True)
    )
    collection_set = set()
    # Handle new collections
    for collection_name in request.POST.getlist("collections[]"):
        collection_set.add(collection_name)
        repo = get_object_or_404(Repository, team__slug=team_slug, type=RepositoryType.COLLECTION, name=collection_name)
        repo.files.add(file)
    file.save(update_fields=["name", "summary"])

    # Remove from collections
    for collection_name in existing_collections - collection_set:
        repo = get_object_or_404(Repository, team__slug=team_slug, type=RepositoryType.COLLECTION, name=collection_name)
        repo.files.remove(file)

    return redirect(reverse("documents:repositories", kwargs={"team_slug": team_slug, "tab_name": "files"}))


class CollectionListView(LoginAndTeamRequiredMixin, BaseObjectListView):
    template_name = "documents/shared/list.html"
    model = Repository
    paginate_by = 10
    details_url_name = "documents:collection_details"
    tab_name = "collections"
    permission_required = "documents.view_repository"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(type=RepositoryType.COLLECTION, team__slug=self.kwargs["team_slug"])
            .order_by("-created_at")
        )

        search = self.request.GET.get("search")
        if search:
            name_similarity = TrigramSimilarity("name", search)
            queryset = (
                queryset.annotate(similarity=name_similarity).filter(Q(similarity__gt=0.2)).order_by("-similarity")
            )
        return queryset


class CollectionDetails(LoginAndTeamRequiredMixin, BaseDetailsView):
    template_name = "documents/collection_details.html"
    model = Repository
    permission_required = "documents.view_repository"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        collection = self.get_object()
        context["edit_url"] = reverse(
            "documents:edit_collection", kwargs={"team_slug": self.kwargs["team_slug"], "id": collection.id}
        )
        context["delete_url"] = reverse(
            "documents:delete_collection", kwargs={"team_slug": self.kwargs["team_slug"], "id": collection.id}
        )
        return context


@require_POST
@transaction.atomic()
def new_collection(request, team_slug: str):
    """Create a new collection"""
    Repository.objects.create(type=RepositoryType.COLLECTION, team=request.team, name=request.POST.get("name"))
    return redirect(reverse("documents:repositories", kwargs={"team_slug": team_slug, "tab_name": "collections"}))


@login_and_team_required
@permission_required("documents.delete_repository")
def delete_collection(request, team_slug: str, id: int):
    collection = get_object_or_404(Repository, team__slug=team_slug, id=id, type=RepositoryType.COLLECTION)
    if pipeline_nodes := collection.get_references():
        response = render_to_string(
            "assistants/partials/referenced_objects.html",
            context={
                "object_name": "collection",
                "pipeline_nodes": [
                    Chip(label=node.pipeline.name, url=node.pipeline.get_absolute_url()) for node in pipeline_nodes
                ],
            },
        )
        return HttpResponse(response, headers={"HX-Reswap": "none"}, status=400)
    else:
        collection.delete()
        messages.success(request, "Collection deleted")
        return HttpResponse()


@login_and_team_required
@require_POST
@permission_required("documents.change_repository")
def edit_collection(request, team_slug: str, id: int):
    collection = get_object_or_404(Repository, team__slug=team_slug, id=id, type=RepositoryType.COLLECTION)
    collection.name = request.POST["name"]
    collection.summary = request.POST["summary"]
    collection.save(update_fields=["name", "summary"])
    return redirect(reverse("documents:repositories", kwargs={"team_slug": team_slug, "tab_name": "collections"}))
