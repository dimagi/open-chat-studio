from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView

from apps.documents.models import Collection
from apps.files.models import File, FilePurpose
from apps.generics.chips import Chip
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search


class CollectionsHome(LoginAndTeamRequiredMixin, TemplateView):
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
            "files_count": File.objects.filter(team__slug=team_slug, purpose=FilePurpose.COLLECTION).count(),
            "max_summary_length": settings.MAX_SUMMARY_LENGTH,
            "supported_file_types": settings.MEDIA_SUPPORTED_FILE_TYPES,
            "collections_count": self.request.team.collection_set.count(),
        }

        if tab_name == "files":
            context["collections"] = self.request.team.collection_set.all()
        return context


class BaseObjectListView(LoginAndTeamRequiredMixin, ListView, PermissionRequiredMixin):
    details_url_name: str

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["details_url_name"] = self.details_url_name
        context["tab_name"] = self.tab_name
        return context

    def get_queryset(self):
        queryset = super().get_queryset().filter(team__slug=self.kwargs["team_slug"]).order_by("-created_at")
        if search := self.request.GET.get("search"):
            queryset = similarity_search(queryset, search_phase=search, columns=["name", "summary"])
        return queryset


class FileListView(BaseObjectListView):
    template_name = "documents/shared/list.html"
    model = File
    paginate_by = 10
    details_url_name = "documents:file_details"
    tab_name = "files"
    permission_required = "files.view_file"

    def get_queryset(self):
        return super().get_queryset().filter(purpose=FilePurpose.COLLECTION)


class BaseDetailsView(LoginAndTeamRequiredMixin, DetailView, PermissionRequiredMixin):
    pass


class FileDetails(BaseDetailsView):
    template_name = "documents/file_details.html"
    model = File
    permission_required = "files.view_file"

    def get_queryset(self):
        return super().get_queryset().filter(team__slug=self.kwargs["team_slug"])

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        file = self.get_object()
        collection_names = file.collection_set.values_list("name", flat=True)
        context["current_collections"] = list(collection_names)
        context["max_summary_length"] = settings.MAX_SUMMARY_LENGTH

        context["edit_url"] = reverse(
            "documents:edit_file", kwargs={"team_slug": self.kwargs["team_slug"], "pk": file.id}
        )
        context["delete_url"] = reverse(
            "documents:delete_file", kwargs={"team_slug": self.kwargs["team_slug"], "pk": file.id}
        )
        context["available_collections"] = self.request.team.collection_set.values_list("name", flat=True)
        return context


@require_POST
@login_and_team_required
@permission_required("files.add_file")
@transaction.atomic()
def upload_files(request, team_slug: str):
    """Upload files to a collection"""
    # TODO: Check collection size and error if it's too large with the new files added
    files = []
    for uploaded_file in request.FILES.getlist("files"):
        files.append(
            File.objects.create(
                team=request.team,
                name=uploaded_file.name,
                file=uploaded_file,
                summary=request.POST[uploaded_file.name],
                purpose=FilePurpose.COLLECTION,
            )
        )

    repo = request.team.collection_set.filter(name=request.POST.get("collection_name"))
    repo.files.add(*files)
    return redirect(reverse("documents:collections", kwargs={"team_slug": team_slug, "tab_name": "files"}))


@login_and_team_required
@permission_required("files.delete_file")
def delete_file(request, team_slug: str, pk: int):
    file = get_object_or_404(File, team__slug=team_slug, id=pk)
    file.delete()
    messages.success(request, "File deleted")
    return HttpResponse()


@require_POST
@login_and_team_required
@permission_required("files.change_file")
@transaction.atomic()
def edit_file(request, team_slug: str, pk: int):
    file = get_object_or_404(File, team__slug=team_slug, id=pk)
    file.name = request.POST.get("name")
    file.summary = request.POST.get("summary")
    file.save(update_fields=["name", "summary"])
    _update_collection_membership(file=file, collection_names=request.POST.getlist("collections"))

    return redirect(reverse("documents:collections", kwargs={"team_slug": team_slug, "tab_name": "files"}))


def _update_collection_membership(file: File, collection_names: list[str]):
    """Handles updating the collections a file belongs to"""
    collections = file.team.collection_set.filter(name__in=collection_names).values_list("id", flat=True)

    existing_collections = set(file.collection_set.values_list("id", flat=True))
    new_collections = set(collections) - existing_collections
    collections_to_remove_files_from = existing_collections - set(collections)

    RepoFileClass = Collection.files.through

    # Handle new collections
    repo_files = []
    for id in new_collections:
        repo_files.append(RepoFileClass(file=file, collection_id=id))

    RepoFileClass.objects.bulk_create(repo_files)

    # Handle outdated collections
    RepoFileClass.objects.filter(file=file, collection_id__in=collections_to_remove_files_from).delete()


class CollectionListView(BaseObjectListView):
    template_name = "documents/shared/list.html"
    model = Collection
    paginate_by = 10
    details_url_name = "documents:collection_details"
    tab_name = "collections"
    permission_required = "documents.view_collection"


class CollectionDetails(BaseDetailsView):
    template_name = "documents/collection_details.html"
    model = Collection
    permission_required = "documents.view_collection"

    def get_queryset(self):
        return super().get_queryset().filter(team__slug=self.kwargs["team_slug"])

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        collection = self.get_object()
        context["edit_url"] = reverse(
            "documents:edit_collection", kwargs={"team_slug": self.kwargs["team_slug"], "pk": collection.id}
        )
        context["delete_url"] = reverse(
            "documents:delete_collection", kwargs={"team_slug": self.kwargs["team_slug"], "pk": collection.id}
        )
        return context


@require_POST
@login_and_team_required
@permission_required("documents.add_collection")
@transaction.atomic()
def new_collection(request, team_slug: str):
    """Create a new collection"""
    try:
        Collection.objects.create(team=request.team, name=request.POST.get("name"))
    except IntegrityError:
        messages.error(request, "A collection with that name already exists.")
    return redirect(reverse("documents:collections", kwargs={"team_slug": team_slug, "tab_name": "collections"}))


@login_and_team_required
@permission_required("documents.delete_collection")
def delete_collection(request, team_slug: str, id: int):
    collection = get_object_or_404(Collection, team__slug=team_slug, id=id)
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


@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
def edit_collection(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection, team__slug=team_slug, id=pk)
    collection.name = request.POST["name"]
    collection.save(update_fields=["name"])
    return redirect(reverse("documents:collections", kwargs={"team_slug": team_slug, "tab_name": "collections"}))
